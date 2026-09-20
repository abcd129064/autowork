# -*- coding: utf-8 -*-
"""单杆视频比分条方向回归测试（2026-09-19 反馈）

反馈现象（选手1/乙方 69 分那一杆生成的视频）：
  1) 顶部左端的「新锐计分」logo 左右反向；
  2) 打杆箭头指向甲方，但单杆分与累计得分都落在乙方一侧 —— 箭头与得分侧不一致。

根因：选手1 的底图是把 image_1.png 整体 transpose 得到的，整体镜像把品牌图形
和「乙方打杆」箭头一起翻到了甲方名牌上。现改为镜像 image_0.png（箭头随镜像落到
乙名牌左缘并转为朝右，与乙方一侧的单杆分一致），品牌图形按镜像坐标贴回未镜像像素。

本文件只校验底图构建（PIL 像素级断言）+ 一次 40 帧真实渲染冒烟，不依赖界面。

运行：<venv>\\Scripts\\python.exe -m pytest tests/test_single_shot_bar.py -v
"""

import ast
import os
import pathlib

import cv2
import numpy as np
import pytest
from PIL import Image

import windows.tools.single_shot_video as ssv

# 打杆箭头所在窗口（960 宽底图）：甲名牌在左半，乙名牌在右半；
# 两个窗口都必须避开中央青赛制块（镜像前后都在 412~547），否则会被误判成箭头
LEFT_WINDOW = (300, 400)
RIGHT_WINDOW = (590, 700)

# 渲染冒烟参数
FRAME_W, FRAME_H, FPS, N_FRAMES = 960, 540, 20, 40
SESSION_CODE = "20260919TESTBARFIX00000000"


def _arr(player):
    """底图像素（int 数组，含 alpha），便于逐像素断言"""
    return np.array(ssv.build_bar_image(player).convert("RGBA")).astype(int)


def _base_arr():
    return np.array(
        Image.open(ssv.resource_path(ssv.BAR_TEMPLATE_NAME)).convert("RGBA")
    ).astype(int)


def _arrow_cols(arr, lo, hi):
    """窗口内青色箭头逐列像素数；窗口内没有箭头返回 None

    阈值收在「青＝R 低、G≈B 且都不高」上：模板箭头是 (0,104,99)，
    叠加到深色帧后按 alpha 变暗成 (0,83,79) 左右；蓝色比分字 (46,107,229)、
    半透明白板 (178,178,178) 都必须被排除，否则会误判成箭头。
    """
    teal = ((arr[:, :, 0] < 90) & (arr[:, :, 1] > 60) & (arr[:, :, 2] > 40)
            & (arr[:, :, 2] < 150) & (arr[:, :, 1] > arr[:, :, 0] + 40)
            & (abs(arr[:, :, 1] - arr[:, :, 2]) <= 40) & (arr[:, :, 3] > 0))
    counts = teal[:, lo:hi].sum(axis=0)
    if counts.max() == 0:
        return None
    nz = np.nonzero(counts)[0]
    return counts[nz.min():nz.max() + 1]


# ==================== 底图构建 ====================

def test_player0_uses_template_untouched():
    """选手0（甲方）沿用模板原样：不打杆方向改动，避免历史效果回归"""
    assert np.array_equal(_arr(0), _base_arr())


def test_player1_layout_is_template_mirrored():
    """选手1（乙方）底图 = 模板镜像：名牌/赛制块位置左右对调"""
    base, bar = _arr(0), _arr(1)
    yellow = lambda a: (a[:, :, 0] > 200) & (a[:, :, 1] > 180) & (a[:, :, 2] < 120) & (a[:, :, 3] > 0)
    row0 = yellow(base)[8]
    row1 = yellow(bar)[8]
    cols0 = np.nonzero(row0)[0]
    cols1 = np.nonzero(row1)[0]
    # 模板名牌 184..339 / 629..783，镜像后 176..330 / 620..775
    assert cols0.min() == 184 and cols0.max() == 783
    assert cols1.min() == 176 and cols1.max() == 775


def test_arrow_points_at_active_player():
    """打杆箭头必须落在打杆者名牌上、并指向该名牌（甲方在左、乙方在右）"""
    base, bar = _arr(0), _arr(1)

    # 选手0：箭头在甲名牌右缘、尖朝左
    a0 = _arrow_cols(base, *LEFT_WINDOW)
    assert a0 is not None, "选手0 底图缺少甲名牌箭头"
    assert _arrow_cols(base, *RIGHT_WINDOW) is None, "选手0 底图乙方侧不应有箭头"
    assert a0[0] < a0[-1], "选手0 箭头应尖朝左（指向甲方）"

    # 选手1：箭头在乙名牌左缘、尖朝右（旧实现整体镜像 image_1，箭头反而留在甲名牌上）
    a1 = _arrow_cols(bar, *RIGHT_WINDOW)
    assert a1 is not None, "选手1 底图缺少乙名牌箭头"
    assert _arrow_cols(bar, *LEFT_WINDOW) is None, "选手1 箭头不应落在甲名牌上"
    assert a1[0] > a1[-1], "选手1 箭头应尖朝右（指向乙方）"


def test_player1_brand_kept_unmirrored():
    """选手1 底图的品牌图形必须是未镜像像素（旧实现整体镜像导致 logo 反向）"""
    base, bar = _arr(0), _arr(1)
    for bx0, by0, bx1, by1 in ssv.BAR_BRAND_BOXES:
        dest = bar.shape[1] - bx1
        patch = bar[:, dest:dest + (bx1 - bx0)]
        src = base[:, bx0:bx1]
        assert np.array_equal(patch, src), f"品牌区 {bx0}..{bx1} 未还原为未镜像像素"
        assert not np.array_equal(patch, src[:, ::-1]), "品牌区仍处于镜像状态"


def test_single_break_slot_side():
    """「单杆N分」空槽：选手0 在左端、选手1 在右端（品牌同时让到另一侧）"""
    base, bar = _arr(0), _arr(1)
    # alpha>200 视为品牌墨迹，空槽处只有半透明白板（179）
    assert (base[:, :150, 3] > 200).sum() == 0, "选手0 底图左端槽位不应有墨迹"
    assert (base[:, -140:, 3] > 200).any(), "选手0 底图品牌应在右端"
    assert (bar[:, :140, 3] > 200).any(), "选手1 底图品牌应在左端"
    assert (bar[:, -150:, 3] > 200).sum() == 0, "选手1 底图右端槽位不应有墨迹"


def test_no_highgui_api_calls():
    """禁止再调用 cv2 HighGUI 接口：当前 opencv 无 GUI 后端，
    destroyAllWindows 会抛 "function is not implemented"，让已写盘成功的视频报失败"""
    tree = ast.parse(pathlib.Path(ssv.__file__).read_text(encoding="utf-8"))
    used = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not (used & {"destroyAllWindows", "imshow", "namedWindow", "waitKey"})


# ==================== 渲染冒烟 ====================

def _write_black_video(path):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                             FPS, (FRAME_W, FRAME_H))
    for _ in range(N_FRAMES):
        writer.write(np.zeros((FRAME_H, FRAME_W, 3), np.uint8))
    writer.release()


def _task(src_video, player):
    total = 10
    return {
        "type": "single_video",
        "file_name": str(src_video),
        "session_name": "第1场",
        "session_code": SESSION_CODE,
        "round": 1,
        "format": "0(3)0",
        "user_ava_0": "playerA.jpg",
        "user_ava_1": "playerB.jpg",
        "user_name_0": "甲方",
        "user_name_1": "乙方",
        "videos": [{
            "text": f"单杆{total}分",
            "player": player,
            "start_frame": 1,
            "end_frame": N_FRAMES,
            "scores": [
                {"score_0": 0, "score_1": 0, "frame_id": 1, "score": 0},
                {"score_0": 0 if player else total,
                 "score_1": total if player else 0,
                 "frame_id": 5, "score": total},
            ],
            "video_name": f"player{player}_single{total}.mp4",
        }],
    }


@pytest.mark.parametrize("player", [0, 1])
def test_render_smoke_bar_side(tmp_path, player):
    """真实渲染 40 帧：任务成功返回 + 比分条叠加在打杆者一侧"""
    src = tmp_path / "src.mp4"
    _write_black_video(src)

    srv = ssv.SingleShotVideoServer(
        source_dir=str(tmp_path), single_video_path=str(tmp_path),
        processed_dir=str(tmp_path), abnormal_dir=str(tmp_path))
    assert srv.single_shot_video(_task(src, player)) is True

    produced = os.path.join(str(tmp_path), SESSION_CODE[:10], SESSION_CODE[:-2],
                            f"player{player}_single10.mp4")
    assert os.path.exists(produced), f"未生成视频: {produced}"

    cap = cv2.VideoCapture(produced)
    ok, frame = cap.read()
    cap.release()
    assert ok, "生成的视频读不出帧"

    # cv2 读回的是 BGR 排列，箭头/名牌判定统一走 RGB 口径
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB).astype(int)
    bar = np.concatenate([rgb[:32], np.full((32, FRAME_W, 1), 255)], axis=2)
    near, far = (LEFT_WINDOW, RIGHT_WINDOW) if player == 0 else (RIGHT_WINDOW, LEFT_WINDOW)
    assert _arrow_cols(bar, *near) is not None, f"选手{player} 视频比分条箭头位置不对"
    assert _arrow_cols(bar, *far) is None, f"选手{player} 视频比分条箭头落到了对手名牌上"
