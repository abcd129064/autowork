# -*- coding: utf-8 -*-
"""单杆视频 json 生成与视频渲染（从 single_json 项目收编）

原 CLI 交互（tkinter 选文件 / input 参数）已由 GUI 对话框替代，
保留核心纯函数 generate_json / extract_break 供 SingleVideoWorker 调用。
"""
import os
import re
import json
import shutil
import logging

from tools.single_shot_video import SingleShotVideoServer

logger = logging.getLogger("SingleShotVideo")

LOG_PATTERN = re.compile(r"frame_id:(\d+)\s+选手(\d+)\s+进(\d+)球")


def extract_break(log_text, start_frame, end_frame, player):
    """解析日志中的单杆得分区间"""
    scores = []
    total = 0

    # 起始占位
    scores.append({
        "score_0": 0,
        "score_1": 0,
        "frame_id": start_frame,
        "score": 0
    })

    for line in log_text.splitlines():
        m = LOG_PATTERN.search(line)
        if not m:
            continue

        frame_id = int(m.group(1))
        p = int(m.group(2))
        s = int(m.group(3))

        if not (start_frame <= frame_id <= end_frame):
            continue
        if p != player:
            continue

        total += s

        scores.append({
            "score_0": 0 if player == 1 else total,
            "score_1": total if player == 1 else 0,
            "frame_id": frame_id,
            "score": s
        })

    return total, scores


def generate_json(
        log_text,
        start_frame,
        end_frame,
        player,
        session_code,
        session_date,
        video_name,
        session_name,
        round_num,
        format_str,
        user_ava_0,
        user_ava_1,
        user_name_0,
        user_name_1,
        pending_root="D:\\pending",
        videos_root="D:\\videos"
):
    """生成单杆 json 并调用 single_shot_video 生成视频

    返回 (True, 视频路径, 视频文件名) 或 False（失败时日志已输出）。
    """
    logger.info("生成单杆 json 并直接调用 single_shot_video 源码生成视频")

    # 查找视频
    src_video_path = os.path.join(pending_root, video_name)
    if not os.path.exists(src_video_path):
        logger.info("视频文件不存在: %s", src_video_path)
        return False
    logger.info("找到视频文件: %s", src_video_path)

    # 创建目录
    target_dir = os.path.join(pending_root, session_date, session_code)
    os.makedirs(target_dir, exist_ok=True)
    logger.info("已创建目录: %s", target_dir)

    # 移动视频
    dst_video_path = os.path.join(target_dir, video_name)
    shutil.move(src_video_path, dst_video_path)
    logger.info("视频已移动到: %s", dst_video_path)

    # 解析单杆
    total, scores = extract_break(log_text, start_frame, end_frame, player)
    logger.info("单杆得分: %d", total)

    # 计算生成的视频文件名和路径
    generated_video_name = f"player{player}_single{total}.mp4"
    # 从session_code中提取前10位作为日期目录名（例如：20260322230533_HF75QY2CNPE10097102W1 -> 2026032223）
    video_dir_date = session_code[:10] if len(session_code) >= 10 else session_date
    # session_code去掉最后2位作为子目录名（例如：20260322230533_HF75QY2CNPE10097102W1 -> 20260322230533_HF75QY2CNPE10097102）
    video_dir_code = session_code[:-2] if len(session_code) > 2 else session_code
    video_output_dir = os.path.join(videos_root, video_dir_date, video_dir_code)
    expected_video_path = os.path.join(video_output_dir, generated_video_name)

    # 生成json
    json_path = os.path.join(target_dir, f"{video_name.split('.')[0]}.json")

    video_json = {
        "type": "single_video",
        "file_name": dst_video_path,
        "session_name": session_name,
        "session_code": session_code,
        "round": round_num,
        "format": format_str,
        "user_ava_0": user_ava_0,
        "user_ava_1": user_ava_1,
        "user_name_0": user_name_0,
        "user_name_1": user_name_1,
        "videos": [
            {
                "text": f"单杆{total}分",
                "player": player,
                "start_frame": start_frame,
                "end_frame": end_frame,
                "scores": scores,
                "video_name": f"player{player}_single{total}.mp4"
            }
        ]
    }

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(video_json, f, ensure_ascii=False, indent=2)

    logger.info("json 已生成: %s", json_path)
    logger.info("预期视频输出路径: %s", expected_video_path)

    # 直接同步调用源码生成视频（不再依赖 exe）
    try:
        logger.info("开始生成单杆视频 ...")
        server = SingleShotVideoServer(
            source_dir=pending_root,
            single_video_path=videos_root,
            processed_dir=pending_root,
            abnormal_dir=pending_root,
        )
        ok = server.single_shot_video(video_json)
        if not ok:
            logger.info("视频生成失败")
            return False
        logger.info("视频生成完成: %s", expected_video_path)
    except Exception as e:
        logger.info("生成视频失败: %s", e)
        return False

    return True, expected_video_path, generated_video_name
