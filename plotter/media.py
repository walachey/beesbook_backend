import os
import tempfile
from subprocess import check_output
import matplotlib
import shutil
import copy
from multiprocessing import Pool
import math

matplotlib.use('Agg')  # need to be executed before pyplot import, deactivates showing of plot in ipython
import matplotlib.pyplot as plt
import matplotlib.patheffects
import numpy as np
from PIL import Image

from . import config
from . import utils
from . import api

def pool():
    if not hasattr(pool, 'p'):
        pool.p = Pool(config.n_threads)
    return pool.p

def adjust_cropping_window(xs, ys, scale, keepaspect=True, padding=600):
    xs, ys = (xs * scale).astype(np.int), (ys * scale).astype(np.int)
    padding *= scale

    width, height = int(config.width * scale), int(config.height * scale)

    left, top, right, bottom = ys.min()-padding, xs.min()-padding,\
                               ys.max()+padding, xs.max()+padding
    
    if keepaspect:
        aspect = width / height
        w, h = right - left, bottom - top
        diff = w - h * aspect
        if diff == 0:
            pass
        if diff < 0:
            left, right = left - abs(diff)//2, right + abs(diff)//2
            if min(width - right, left) < 0:
                diff = abs(left) if left < 0 else width - right
                left, right = left + diff, right + diff
        elif diff > 0:
            diff = abs(diff) / aspect
            top, bottom = top - diff // 2, bottom + diff // 2
            if min(height - bottom, top) < 0:
                diff = abs(top) if top < 0 else height - bottom
                top, bottom = top + diff, bottom + diff

    left, top, right, bottom = [x + x % 2 for x in (left, top, right, bottom)]  # make numbers even for ffmpeg
    left, top, right, bottom = max(left, 0), max(top, 0), min(right, width), min(bottom, height)
    return left, top, right, bottom


@utils.buffer_object_cacher(key=lambda frame, scale: (frame.frame_id, scale), maxsize=8)
def extract_single_frame(frame, scale):
    """
    Extracts the image belonging to a `Frame`-object.
    Args:
        frame (Frame): The frame which should be extracted.

    Returns:
        An utils.ReusableBytesIO object containing the image.

    """
    with tempfile.NamedTemporaryFile(suffix=".jpg") as tmpfile:

        cmd = config.ffmpeg_extract_single_frame.format(
            video_path=frame.fc.video_path,
            frame_index=frame.index,
            output_path=tmpfile.name,
            scale=scale
        )
        print('executing: ', cmd)
        output = check_output(cmd, shell=True)
        print('output:', output)

        with open(tmpfile.name, "rb") as file:
            buf = utils.ReusableBytesIO(file.read())
            buf.seek(0)
            return buf

@utils.buffer_object_cacher(key=lambda framecontainer, scale: (framecontainer.video_path, scale), maxsize=4)
def extract_frames(framecontainer, scale):
    """
    Extracts all frame-images of the corresponding video file of a FrameContainer.

    Args:
        framecontainer (FrameContainer): The FrameContainer which represents the video file from which the frames
         should be extracted

    Returns:
        Dictionary with a mapping of Frame.id to utils.ReusableBytesIO object containing the frame.

    """
    video_name = framecontainer.video_name

    # Required frames.
    # Subset of the resulting filenames of the ffmpeg command.
    frame_set = framecontainer.frame_set
    images = ['{:04}.jpg'.format(x) for x in frame_set.values_list('index', flat=True)]
    
    results = dict()

    with tempfile.TemporaryDirectory() as tmpdir:

        cmd = config.ffmpeg_extract_all_frames.format(
            video_path=framecontainer.video_path, output_path=tmpdir, scale=scale)
        print('executing: ', cmd)
        output = check_output(cmd, shell=True)
        print('output:', output)
        
        for idx, frame in enumerate(frame_set.all()):
            with open(os.path.join(tmpdir, images[idx]), "rb") as file:
                output = utils.ReusableBytesIO(file.read())
                output.seek(0)
                results[frame.frame_id] = output
    return results


def extract_video(frames):
    """
    Extracts a number of frames and makes a video.
    Args:
        frames (list:Frame): list of frames

    Returns:
        The video as a utils.ReusableBytesIO object.
    """
    with tempfile.TemporaryDirectory() as tmpdir:

        for i, frame in enumerate(frames):
            buffer = frame.get_image(extract='all')
            output_path = os.path.join(tmpdir, f'{i:04}.jpg')
            with open(output_path, "wb") as file:
                shutil.copyfileobj(buffer, file)

        with tempfile.NamedTemporaryFile(suffix=".mp4") as tmpfile:

            cmd = config.ffmpeg_frames_to_video.format(
                input_path=f'{tmpdir}/%04d.jpg',
                output_path=tmpfile.name,
                framerate=3
            )
            print('executing: ', cmd)
            check_output(cmd, shell=True)
            
            with open(tmpfile.name, "rb") as file:
                output = utils.ReusableBytesIO(file.read())
                output.seek(0)
                return output

def rotate_direction_vec(rotation, scale):
    x, y = 0, 5 / scale
    sined = np.sin(rotation)
    cosined = np.cos(rotation)
    normed_x = x*cosined - y*sined
    normed_y = x*sined + y*cosined
    return np.around(normed_x, decimals=2), np.around(normed_y, decimals=2)


class FramePlotter(api.FramePlotter):

    # Internal attributes.
    _xs_scaled = None
    _ys_scaled = None
    _cam_id = None
    _timestamp = None

    def __init__(self, **args):
        super(FramePlotter, self).__init__(**args)

        if self._colors is not None:
            self._colors = np.array(self._colors)
        if self._labels is not None:
            self._labels = np.array(self._labels)

    # Wrap the internal properties in case they require post-processing.

    @property
    def xs(self):
        if not self._xs:
            return None
        if self._xs_scaled is None:
            self._xs_scaled = (np.array(self._xs) * self.scale).astype(np.int)
        return self._xs_scaled
    @property
    def ys(self):
        if not self._ys:
            return None
        if self._ys_scaled is None:
            self._ys_scaled = (np.array(self._ys) * self.scale).astype(np.int)
        return self._ys_scaled
    @property
    def xs_unscaled(self):
        return self._xs
    @property
    def ys_unscaled(self):
        return self._ys
    @property
    def angles(self):
        return self._angles
    @property
    def sizes(self):
        if self._sizes is None:
            return None
            self._sizes = np.array(self._sizes) / self.scale
        return self._sizes
    @property
    def colors(self):
        if self._colors is None:
            self._colors = np.array(["yellow"] * self.xs.shape[0])
        return self._colors
    @property
    def labels(self):
        return self._labels
    @property
    def title(self):
        return self._title
    @property
    def frame_id(self):
        return self._frame_id
    @property
    def scale(self):
        return self._scale if self._scale else 0.5
    @property
    def crop_coordinates(self):
        if self._crop_coordinates is None:
            return self._crop_coordinates
        return list(np.array(self._crop_coordinates) * self.scale)
    @property
    def width(self):
        return int(config.width * self.scale)
    @property
    def height(self):
        return int(config.height* self.scale)
    @property
    def path_alpha(self):
        return self._path_alpha or 0.25
    
    def prepare_plotting(self, frame_obj):
        """
            Required to be called prior to plotting. Fetches
            certain information from the database so that a forked
            process does not need to access the database objects or
            the connection.

            Args:
                frame_obj: models.Frame object
        """
        self._timestamp = frame_obj.timestamp
        self._cam_id = frame_obj.cam_id

    def calculate_origin(self, frame_obj):
        
        import datetime
        assert self._cam_id >= 0 and self._cam_id <= 3
        year = datetime.datetime.utcfromtimestamp(self._timestamp).year
        
        if year == 2016:
            return [(0, 1), (1, 0), (0, 1), (1, 0)][self._cam_id]

        return (0, 0)

    def plot(self, buffer, frame_obj=None):
        """

        Args:
            buffer: file-like object containing the image

        Returns:
            utils.ReusableBytesIO object containing the final image
        """
        if frame_obj is not None:
            self.prepare_plotting(frame_obj)
        else:
            if self._cam_id is None:
                raise ValueError("FramePlotter.plot called without frame_obj and without having called prepare_plotting beforehand.")

        outputbuffer = None

        fig, ax = plt.subplots()
        dpi = fig.get_dpi()
        fig.set_size_inches(self.width/dpi, self.height/dpi)
        fig.subplots_adjust(left=0, right=1, bottom=0, top=1)  # removes white margin
        image = np.swapaxes(plt.imread(buffer, format="JPG"), 0, 1)
        ax.imshow(image)
        ax.axis('off')

        # To be able to specify a size independent of the resolution.
        width = image.shape[1]
        height = image.shape[0]
        if self.crop_coordinates:
            x, y, x2, y2 = self.crop_coordinates
            width = x2 - x
            height = y2 - y
        width_factor = 1.0 / (width / self.scale / config.width)

        if self.xs is not None and self.ys is not None:
            # Draw arrows if rotation is given.
            if self.angles is not None:
                rotations = np.array([rotate_direction_vec(rot, self.scale) for rot in self.angles])
                ax.quiver(self.ys, self.xs, rotations[:, 1], rotations[:, 0], scale=0.45 / self.scale, color=self.colors, units='xy', alpha=0.5)
            
            for unique_color in np.unique(self.colors):
                idx = self.colors == unique_color

                # Draw scatterplot if radius is given.
                if self.sizes is not None:
                    radius = np.array(self.sizes)
                    # The size is meant to be in pixels of the original video.
                    # A radius of around 25 pixels would be a tag.
                    size = 2.0 * float(radius[idx][0])
                    # Adjust for cropping region.
                    # Usually the markersize scales with the window.
                    size *= width_factor
                    # Calcluate area, adjusted for scaling factor.
                    size = (size * self.scale) ** 2.0
                    ax.scatter(self.ys[idx], self.xs[idx], facecolors='none', edgecolors=unique_color, marker="o",
                      s=size, linewidth=5 * self.scale, alpha=0.5)
                # Draw marker labels if given.
                if self.labels is not None:
                      for i, label_i in enumerate(self.labels[idx]):
                        if label_i is None or not label_i:
                            continue
                        ax.text(self.ys[idx][i], self.xs[idx][i], label_i, color=unique_color, fontsize=int(72 * self.scale), alpha=0.5)
        if self._paths is not None:
            for label, (distance, path) in self._paths.items():
                path = np.array(path)
                color = "k"
                try:
                    label_idx = np.argwhere(self.labels == label)[0][0]
                    color = self.colors[label_idx]
                except:
                    pass
                # Plot the path in segments to allow fading alpha.
                # Use bigger steps for better performance.
                last_end = path.shape[0]
                stepsize = 10 if last_end > 20 else 4
                steps = list(reversed(range(0, last_end, stepsize)))
                alpha = 1.0 - 0.1 * np.arange(len(steps))
                alpha[alpha < 0.1] = 0.1
                alpha *= self.path_alpha
                
                for step_i, step in enumerate(steps):
                    ax.plot(path[step:last_end,1], path[step:last_end,0], color=color, linewidth=10.0 / width_factor * self.scale, alpha=alpha[step_i])
                    last_end = step + 1
        if self.title is not None:
            txt = plt.text(0.1, 0.9, self.title, size=int(108 * self.scale), color='white', transform=ax.transAxes, horizontalalignment='left')
            txt.set_path_effects([matplotlib.patheffects.withStroke(linewidth=5, foreground='k')])
        if self.crop_coordinates is not None:
            x, y, x2, y2 = self.crop_coordinates
            ax.set_xlim((y, y2))
            ax.set_ylim((x, x2))
        else:
            # Make sure that the plot is cropped at the image's bounds.
            ax.set_xlim((0, image.shape[1]))
            ax.set_ylim((0, image.shape[0]))
        # Make sure that the image's origin is the same as in the original video.
        origin = self.calculate_origin(frame_obj)
        if origin[0] == 1:
            plt.gca().invert_xaxis()
        if origin[1] == 0:
            plt.gca().invert_yaxis()

        outputbuffer = utils.ReusableBytesIO()
        fig.savefig(outputbuffer, dpi=dpi, format='JPG')
        plt.close()

        outputbuffer.seek(0)

        if False and self.crop_coordinates is not None:
            im = Image.open(outputbuffer)
            im = im.crop(self.crop_coordinates)
        
            outputbuffer.seek(0)
            im.save(outputbuffer, format='JPEG')
            outputbuffer.seek(0)

        return outputbuffer


class VideoPlotter(api.VideoPlotter):
    
    def __init__(self, **args):
        super(VideoPlotter, self).__init__(**args)

        # 'frames' can be a list of dictionaries, too.
        if len(self._frames) > 0 and isinstance(self._frames[0], dict):
            self._frames = [FramePlotter.from_dict(frame) for frame in self._frames]

        # First, fill in missing frames if requested.
        if self._fill_gaps:
            from .models import Frame

            fids = [frame.frame_id for frame in self._frames]
            i = 0
            while i < len(fids) - 1:
                fid1, fid2 = fids[i], fids[i+1]
                f1 = Frame.objects.get(frame_id=fid1)
                f2 = Frame.objects.get(frame_id=fid2)
                if f1.fc_id != f2.fc_id:
                    i += 1
                    continue
                if f2.index - f1.index == 1:
                    i += 1
                    continue
                fill_frame_ids = (
                    Frame.objects.filter(
                        fc_id=f1.fc_id,
                        index__gt=f1.index,
                        index__lt=f2.index
                    ).order_by('index').values_list('frame_id', flat=True)
                )
                for fill_frame_id in reversed(fill_frame_ids):  # reversed so we dont need to increment i
                    fill_frame_id = int(fill_frame_id)
                    fids.insert(i+1, fill_frame_id)
                    # Fill data with copy of previous frame.
                    filler_frame = copy.deepcopy(self._frames[i])
                    filler_frame._frame_id = fill_frame_id
                    self._frames.insert(i+1, filler_frame)
                i += 1 + len(fill_frame_ids)
        
        # Add frames before and after the specified frames.
        if self._n_frames_before_after:
            from .models import Frame
            for idx, offset in ((0, -_n_frames_before_after-1), (-1, +_n_frames_before_after+1)):
                fid = self._frames[idx].frame_id
                frame = Frame.objects.get(frame_id=fid)
                from_idx, to_idx = frame.index + offset, frame.index
                if offset > 0:
                    from_idx, to_idx = to_idx, from_idx
                fill_frame_ids = (
                    Frame.objects.filter(
                        fc_id=frame.fc_id,
                        index__gt=from_idx,
                        index__lt=to_idx
                    ).order_by('index').values_list('frame_id', flat=True)
                )
                if idx == 0:
                    fill_frame_ids = reversed(fill_frame_ids)
                for fill_frame_id in fill_frame_ids:
                    filler_frame = copy.deepcopy(frame)
                    filler_frame._xs = None
                    filler_frame._ys = None
                    filler_frame._title = None
                    self._frames.insert(idx, filler_frame)

        # Calculate auto-cropping.
        if self._crop_margin is not None:
            scale = self._scale
            if scale is None and len(self._frames) > 0:
                scale = self._frames[0].scale
            xs = np.array([x for frame in self._frames for x in frame._xs])
            ys = np.array([y for frame in self._frames for y in frame._ys])
            self._crop_coordinates = adjust_cropping_window(xs, ys,
                                        scale=scale, padding=self._crop_margin)

        # Calculate tracks based on the labels.
        if self._track_labels:
            # First pass, figure out positions of labels per frame.
            for frame_idx, frame in enumerate(self._frames):
                if frame.labels is None:
                    continue
                if not frame._paths:
                    frame._paths = {}
                # For every label in the current frame, find the closest matching
                # label in the next frames.
                for label_idx, label in enumerate(frame.labels):
                    candidates = []
                    label_x, label_y = frame.xs_unscaled[label_idx], frame.ys_unscaled[label_idx]
                    # Need to start a new path?
                    current_path = (math.inf, [[label_x, label_y]])
                    if label in frame._paths:
                        current_path = frame._paths[label]
                    
                    for next_frame_idx in range(frame_idx + 1, len(self._frames)):
                        next_frame = self._frames[next_frame_idx]
                        if next_frame.labels is None:
                            continue
                        frame_distance = next_frame_idx - frame_idx

                        # Figure out index of label(-candidates) in next frame.
                        for other_label_idx, other_label in enumerate(next_frame.labels):
                            if other_label == label:
                                x, y = next_frame.xs_unscaled[other_label_idx], next_frame.ys_unscaled[other_label_idx]
                                distance = math.sqrt((label_x - x) ** 2.0 + (label_y - y) ** 2.0)
                                # Allow only a sensible distance to prevent lines from jumping.
                                # Per-frame movement limit.
                                if distance > (frame_distance * 75.0):
                                    continue
                                # Total gap length before a new path is started.
                                if distance > 300.0:
                                    continue
                                candidates.append((distance, next_frame_idx, (x, y)))
                        if candidates:
                            break
                    if not candidates:
                        continue
                    # Now remember the line for the nearest next label.
                    distance, next_frame_idx, (x, y) = sorted(candidates)[0]
                    # And interpolate all frames in between.
                    interpolation_per_frame = 1.0 / float(next_frame_idx - frame_idx)
                    for f, interpolation_frame_idx in enumerate(range(frame_idx + 1, next_frame_idx + 1)):
                        next_frame = self._frames[interpolation_frame_idx]
                        interpolation = (f + 1) * interpolation_per_frame
                        _x = label_x + (x - label_x) * interpolation
                        _y = label_y + (y - label_y) * interpolation

                        if not next_frame._paths:
                            next_frame._paths = {}
                        # Check if better path was found.
                        if label in next_frame._paths:
                            if distance >= next_frame._paths[label][0]:
                                break
                        new_path = (distance, current_path[1] + [[_x, _y]])
                        next_frame._paths[label] = new_path
                        current_path = new_path

        # Some options can be set for all frames through the video options.
        for property in ("_crop_coordinates", "_scale", "_path_alpha"):
            value = getattr(self, property)
            if value is not None:
                for frame in self._frames:
                    if getattr(frame, property) is None:
                        setattr(frame, property, value)

        # If a title prefix is specified, update the frames.
        if self._title and len(self._frames) > 0:
            import datetime
            prefix = self._title
            
            if prefix == "auto":
                # Figure out the cam ID - assume that all frames come from the same cam.
                frame = Frame.objects.get(frame_id=self._frames[0].frame_id)
                cam_id = frame.cam_id
                # The actual frame ID and datetime will be added later.
                prefix = "{frame_idx:4d} {datetime:}"
                # Only the cam is fixed for all frames.
                prefix += f" cam {cam_id:2d}"
            # Whether we need to query additional metadata for the titles.
            needs_frame_info = ("{datetime" in prefix)

            for frame_idx, frame in enumerate(self._frames):
                # Fill placeholders.
                format_args = {}
                if "{frame_idx" in prefix:
                    format_args["frame_idx"] = frame_idx
                if needs_frame_info:
                    db_frame = Frame.objects.get(frame_id=frame.frame_id)
                    if "{datetime" in prefix:
                        format_args["datetime"] = \
                            datetime.datetime.fromtimestamp(db_frame.timestamp).\
                            strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
                custom_prefix = prefix
                if format_args:
                    custom_prefix = custom_prefix.format(**format_args)

                if frame._title:
                    frame._title = custom_prefix + " " + frame._title
                else:
                    frame._title = custom_prefix
    def plot(self):
        """
        Creates a video with information of a track

        Returns:
            utils.ReusableBytesIO object containing the video.
        """
        from .models import Frame

        results = []
        extracted_frames = dict()
        for plotter in self._frames:
            frame = Frame.objects.get(frame_id=plotter.frame_id)

            if frame.frame_id not in extracted_frames:
                extracted_frames = {**extracted_frames, **extract_frames(frame.fc, plotter.scale)}
                assert(frame.frame_id in extracted_frames)
            # Prepare non-fork-safe things.
            plotter.prepare_plotting(frame)

            r = pool().apply_async(
                plotter.plot,
                (extracted_frames[frame.frame_id],)
            )
            results.append(r)

        images = [r.get() for r in results]  # wait for all

        with tempfile.TemporaryDirectory() as tmpdir:
            # Write buffer to disk for ffmpeg to work.
            for idx, buffer in enumerate(images):
                with open(os.path.join(tmpdir, f'{idx:04d}.jpg'), "wb") as file:
                    shutil.copyfileobj(buffer, file)
        
            input_path = os.path.join(tmpdir, '%04d.jpg')
            video_output_path = os.path.join(tmpdir, 'video.mp4')
            framerate = self._framerate or 3
            cmd = config.ffmpeg_frames_to_video.format(input_path=input_path, output_path=video_output_path, framerate=framerate)
            print('executing: ', cmd)
            output = check_output(cmd, shell=True)
            print('Output:', output)

            with open(video_output_path, "rb") as file:
                buf = utils.ReusableBytesIO(file.read())
                buf.seek(0)
                return buf
