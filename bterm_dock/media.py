"""Локальные видео и импорт фоновых файлов по прямой ссылке."""

from functools import lru_cache
import hashlib
from http.client import HTTPException
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from time import monotonic
from urllib.error import URLError
from urllib.parse import unquote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ._gtk import GdkPixbuf, GLib

IMAGE_FORMATS = {"png": ".png", "gif": ".gif", "jpeg": ".jpg"}
MAX_DOWNLOAD_SIZE = 100 * 1024 * 1024


def fit_pixbuf(pixbuf, width, height):
    original_width, original_height = pixbuf.get_width(), pixbuf.get_height()
    scale = min(width / original_width, height / original_height)
    size = max(1, round(original_width * scale)), max(1, round(original_height * scale))
    if size == (original_width, original_height):
        return pixbuf
    return pixbuf.scale_simple(*size, GdkPixbuf.InterpType.BILINEAR)


def load_thumbnail(path, width, height):
    if Path(path).suffix.lower() == ".gif":
        image = GdkPixbuf.PixbufAnimation.new_from_file(path).get_static_image()
        return fit_pixbuf(image, width, height)
    return GdkPixbuf.Pixbuf.new_from_file_at_scale(path, width, height, True)


@lru_cache(maxsize=1)
def gstreamer():
    import gi

    try:
        for name in ("Gst", "GstPbutils", "GstVideo"):
            gi.require_version(name, "1.0")
        from gi.repository import Gst, GstPbutils, GstVideo
    except (ImportError, ValueError) as error:
        raise ValueError("Для видео MP4 нужны GStreamer, его библиотеки GI и декодеры MP4.") from error
    Gst.init(None)
    if Gst.version()[:2] < (1, 20):
        raise ValueError("Для видео MP4 нужен GStreamer 1.20 или новее.")
    if not all(Gst.ElementFactory.find(name) for name in ("playbin", "appsink")):
        raise ValueError("Для видео MP4 нужны плагины GStreamer Base и Good.")
    return Gst, GstPbutils, GstVideo


class VideoSource:
    def __init__(self, path):
        self.uri = Path(path).absolute().as_uri()
        Gst, GstPbutils, _ = gstreamer()
        info = GstPbutils.Discoverer.new(4 * Gst.SECOND).discover_uri(self.uri)
        videos = info.get_video_streams()
        if not videos or info.get_result() != GstPbutils.DiscovererResult.OK:
            raise ValueError("Не удалось прочитать видео MP4. Проверьте файл и декодеры GStreamer.")
        self.width = videos[0].get_width()
        self.height = videos[0].get_height()


class VideoPlayer:
    def __init__(self, source, show_frame, show_error):
        Gst, _, self.video_api = gstreamer()
        self.gst = Gst
        self.show_frame = show_frame
        self.show_error = show_error
        self.timer = 0
        self.pipeline = Gst.ElementFactory.make("playbin")
        self.sink = Gst.ElementFactory.make("appsink")
        if self.pipeline is None or self.sink is None:
            raise ValueError("Для видео MP4 нужны плагины GStreamer Base и Good.")
        self.sink.set_property("caps", Gst.Caps.from_string(
            "video/x-raw,format=RGB,width=(int)[1,1280],height=(int)[1,720],pixel-aspect-ratio=1/1"
        ))
        self.sink.set_property("max-buffers", 1)
        self.sink.set_property("drop", True)
        self.pipeline.set_property("video-sink", self.sink)
        self.pipeline.set_property("flags", 1)  # Только видео, без звука и субтитров.
        self.pipeline.set_property("uri", source.uri)
        self.bus = self.pipeline.get_bus()
        self.bus.add_signal_watch()
        self.handler = self.bus.connect("message", self.on_message)

    def play(self):
        if self.pipeline is not None and not self.timer:
            self.pipeline.set_state(self.gst.State.PLAYING)
            self.timer = GLib.timeout_add(33, self.read_frame)

    def pause(self):
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = 0
        if self.pipeline is not None:
            self.pipeline.set_state(self.gst.State.PAUSED)

    def close(self):
        if self.pipeline is not None:
            self.pause()
            self.bus.disconnect(self.handler)
            self.bus.remove_signal_watch()
            self.pipeline.set_state(self.gst.State.NULL)
            self.pipeline = self.sink = self.bus = None

    def on_message(self, _, message):
        if message.type == self.gst.MessageType.ERROR:
            error, _ = message.parse_error()
            self.close()
            self.show_error(error.message)
        elif message.type == self.gst.MessageType.EOS and self.pipeline is not None:
            self.pipeline.seek_simple(self.gst.Format.TIME,
                                      self.gst.SeekFlags.FLUSH | self.gst.SeekFlags.KEY_UNIT, 0)

    def read_frame(self):
        sample = self.sink.emit("try-pull-sample", 0)
        if sample is not None:
            info = self.video_api.VideoInfo.new_from_caps(sample.get_caps())
            buffer = sample.get_buffer()
            data = GLib.Bytes.new(buffer.extract_dup(0, buffer.get_size()))
            pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(data, GdkPixbuf.Colorspace.RGB, False, 8,
                                                   info.width, info.height, info.stride[0])
            self.show_frame(pixbuf)
        return GLib.SOURCE_CONTINUE


def check_url(url):
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username is not None:
        raise ValueError("Вставьте прямую HTTP(S)-ссылку на файл.")
    return parts


class MediaRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_media(url, cancelled):
    check_url(url)
    folder = Path(GLib.get_user_data_dir()) / "bterm-dock" / "media"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    request = Request(url, headers={"User-Agent": "BTermDock"})
    digest = hashlib.sha256()
    deadline = monotonic() + 90
    with NamedTemporaryFile(dir=folder, prefix=".download-", delete=False) as output:
        temporary = Path(output.name)
        try:
            try:
                with build_opener(MediaRedirect).open(request, timeout=12) as response:
                    parts = check_url(response.geturl())
                    size = 0
                    while not cancelled.is_set():
                        chunk = response.read1(128 * 1024)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > MAX_DOWNLOAD_SIZE:
                            raise ValueError("Файл по ссылке превышает 100 МБ.")
                        if monotonic() >= deadline:
                            raise ValueError("Загрузка заняла слишком много времени.")
                        digest.update(chunk)
                        output.write(chunk)
            except (URLError, HTTPException, TimeoutError) as error:
                raise ValueError("Не удалось скачать файл по ссылке.") from error
            if cancelled.is_set():
                return None
            output.close()
            image_format, _, _ = GdkPixbuf.Pixbuf.get_file_info(str(temporary))
            if image_format and image_format.get_name() in IMAGE_FORMATS:
                extension = IMAGE_FORMATS[image_format.get_name()]
            else:
                with temporary.open("rb") as file:
                    header = file.read(16)
                if header[4:8] != b"ftyp":
                    raise ValueError("По ссылке должен быть сам файл PNG, JPG, GIF или MP4.")
                VideoSource(temporary)
                extension = ".mp4"
            if cancelled.is_set():
                return None
            name = Path(unquote(parts.path)).stem
            name = re.sub(r"[^\w .-]", "-", name).strip(" .-")[:40] or "фон"
            target = folder / f"{name}-{digest.hexdigest()[:16]}{extension}"
            temporary.replace(target)
            return target
        finally:
            temporary.unlink(missing_ok=True)
