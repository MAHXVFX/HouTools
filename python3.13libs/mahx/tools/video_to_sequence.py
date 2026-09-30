"""视频转序列图工具入口。

将 mov/mp4/avi/mkv 等视频用 ffmpeg 转为 JPG 序列，
可选自动设置场景相机的 Background Image。
"""


def run():
    from mahx.videoseq.window import show_video_to_sequence_window
    show_video_to_sequence_window()
