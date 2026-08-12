# -*- coding: utf-8 -*-
"""单杆视频渲染服务（从 single_json 项目收编，Task: 工具菜单「单杆视频」）

原实现依赖 loguru，收编后统一改用标准 logging（模块级 logger 名为
"SingleShotVideo"，由 SingleVideoWorker 挂临时 Handler 逐行转发到 GUI），
资源文件（字体/头像/底图/logo）从 autowork 的 resource/ 目录读取。
"""
import os
import gc
import re
import json
import shutil
import logging

from PIL import Image, ImageDraw, ImageFont
import numpy as np
import cv2

# 模块级 logger：GUI Worker 通过 logging.getLogger("SingleShotVideo") 捕获转发
logger = logging.getLogger("SingleShotVideo")

# 单杆资源目录：开发环境为项目根/resource，打包环境为 _internal/resource
try:
    from core.app_paths import get_resource_dir
    RESOURCE_DIR = os.path.join(get_resource_dir(), "resource")
except Exception:  # 独立运行兜底：本文件所在目录
    RESOURCE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "resource")


def resource_path(name):
    """解析单杆资源文件路径（位于项目 resource 目录，兼容 PyInstaller 打包）"""
    return os.path.join(RESOURCE_DIR, name)


class SingleShotVideoServer:

    def __init__(self, source_dir, single_video_path, processed_dir, abnormal_dir):
        self.source_dir = source_dir  # 监控目录
        self.processed_dir = processed_dir  # 已处理目录
        self.abnormal_dir = abnormal_dir  # 异常目录
        self.single_video_path = single_video_path  # 单杆路径

    def move_to_processed(self, file_path, type):
        try:
            if type == 1:
                # 如果目标路径已存在，自动添加 `_1`, `_2` 后缀
                base_name = os.path.basename(file_path)
                counter = 1
                new_dst_path = os.path.join(self.processed_dir, base_name)
                while os.path.exists(new_dst_path):
                    logger.info("{}文件夹已存在".format(new_dst_path))
                    new_dst_path = os.path.join(
                        self.processed_dir, "{}_{}".format(base_name, counter))
                    counter += 1
                shutil.move(file_path, new_dst_path)
        except Exception as e:
            logger.error("迁移文件失败：{}".format(e))

    # 处理指定文件夹中的所有 json 任务（读取 json -> 生成视频 -> 删除 json）
    def process_folder(self, folder_path):
        if not os.path.isdir(folder_path):
            logger.error("文件夹不存在: {}".format(folder_path))
            return False
        for filename in os.listdir(folder_path):
            json_path = os.path.join(folder_path, filename)
            if not (filename.endswith('.json') and os.path.isfile(json_path)):
                continue
            try:
                with open(json_path, 'r', encoding='utf-8') as fp:
                    logger.info("{}.{} open success".format(
                        filename.split(".")[0], "json"))
                    cfg = json.load(fp)
                # 注意：文件关闭后再删除，Windows 不允许删除仍被打开的文件
                result = self.single_shot_video(cfg)
                os.remove(json_path)  # 删除文件
            except json.JSONDecodeError as e:
                logger.error(f"JSON 解析失败: {e}")
            except FileNotFoundError:
                logger.error("文件不存在，请检查路径")
        return True

    # 单次处理模式：只制作当前生成的文件夹，处理完即退出（不再轮询）
    # target_folder 指定时只处理该文件夹；否则扫描 source_dir 下含 json 的文件夹（兼容 日期/代码 两级结构）
    def monitor_directory(self, target_folder=None):
        if target_folder is not None:
            # 只制作指定文件夹
            self.process_folder(target_folder)
            return

        # 单次扫描：只处理含 json 的文件夹，处理完退出
        for file_path in os.listdir(self.source_dir):
            first_src = os.path.join(self.source_dir, file_path)
            if not os.path.isdir(first_src):
                continue
            for second_file_path in os.listdir(first_src):
                second_src = os.path.join(first_src, second_file_path)
                if os.path.isdir(second_src) and any(
                        f.endswith('.json') for f in os.listdir(second_src)):
                    self.process_folder(second_src)

    def load_image_from_url(self, image_url, user):
        # 获取图像内容
        import requests
        if image_url[:4] == "http":
            response = requests.get(image_url)

            # 确保请求成功
            if response.status_code == 200:
                # 将响应内容转换为 numpy 数组
                img_array = np.frombuffer(response.content, np.uint8)
                # 使用 cv2 解码图像
                img = cv2.imdecode(img_array, cv2.IMREAD_UNCHANGED)
                return img
            else:
                logger.error("无法从 URL 加载图像")
                avatar = None
                if user == 0:
                    avatar = cv2.imread(resource_path("playerA.jpg"), cv2.IMREAD_UNCHANGED)
                if user == 1:
                    avatar = cv2.imread(resource_path("playerB.jpg"), cv2.IMREAD_UNCHANGED)

                if avatar is None:
                    logger.error("无法打开头像文件")
                    return None
                return avatar
        else:
            # 非 http 路径：优先使用存在的文件路径，找不到相对文件时回退到资源目录，避免 imread 盲读触发警告
            image_path = image_url
            if not os.path.exists(image_path) and not os.path.isabs(image_path):
                fallback = resource_path(image_url)
                if os.path.exists(fallback):
                    image_path = fallback
            avatar = cv2.imread(image_path, cv2.IMREAD_UNCHANGED)
            if avatar is None:
                logger.error("无法打开头像文件: {}".format(image_url))
                return None
            return avatar

    def single_shot_video(self, task):
        import os
        try:
            frame_count = 0
            videos = task['videos']
            file_name = task['file_name']
            session_code = task['session_code']

            # 遍历所有的视频
            for video in videos:
                # 获取当前视频的 scores 数组
                scores = video["scores"]

                # 获取最后一条分数记录
                if scores:  # 确保 scores 不为空
                    last_score = scores[-1]

                    # 创建新的分数记录
                    new_score = last_score.copy()  # 复制最后一条记录
                    new_score["frame_id"] += 25  # 增加 frame_id

                    # 将新记录添加到 scores 数组
                    scores.append(new_score)

            # 读取视频
            cap = cv2.VideoCapture(file_name)
            if not cap.isOpened():
                logger.error("无法打开视频文件")
                return False

            for item in videos:
                text = item['text']
                user_0 = task['user_name_0'] if len(task['user_name_0']) < 5 else task['user_name_0'][:5] + "..."
                user_1 = task['user_name_1'] if len(task['user_name_1']) < 5 else task['user_name_1'][:5] + "..."

                # 读取左侧头像
                left_avatar = self.load_image_from_url(task['user_ava_0'], 0)
                if left_avatar is None:
                    logger.error("无法左侧头像文件")
                    return False

                # 读取右侧头像
                right_avatar = self.load_image_from_url(task['user_ava_1'], 1)
                if right_avatar is None:
                    logger.error("无法右侧头像文件")
                    return False

                logo = self.load_image_from_url(resource_path("logo.png"), 2)
                if logo is None:
                    logger.error("无法logo文件")
                    return False

                # 确保图像是 3 通道（RGB），否则转换
                if len(left_avatar.shape) == 2:  # 灰度图，转换为 RGB
                    left_avatar = cv2.cvtColor(left_avatar, cv2.COLOR_GRAY2RGB)
                if left_avatar.shape[2] == 3:  # 如果是 RGB，则添加 Alpha 通道
                    left_avatar = cv2.cvtColor(left_avatar, cv2.COLOR_RGB2RGBA)

                if len(right_avatar.shape) == 2:
                    right_avatar = cv2.cvtColor(right_avatar, cv2.COLOR_GRAY2RGB)
                if right_avatar.shape[2] == 3:
                    right_avatar = cv2.cvtColor(right_avatar, cv2.COLOR_RGB2RGBA)

                if len(logo.shape) == 2:
                    logo = cv2.cvtColor(logo, cv2.COLOR_GRAY2RGB)
                if logo.shape[2] == 3:
                    logo = cv2.cvtColor(logo, cv2.COLOR_RGB2RGBA)

                # 获取视频的宽、高和帧率
                frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                fps = int(cap.get(cv2.CAP_PROP_FPS))

                # 调整头像大小
                left_avatar = cv2.resize(left_avatar, (32, 32))
                right_avatar = cv2.resize(right_avatar, (32, 32))
                logo = cv2.resize(logo, (60, 60))

                avatar_h, avatar_w, avatar_channels = left_avatar.shape
                logo_h, logo_w, logo_channels = logo.shape

                # 左侧头像位置
                left_x_offset = 154  # 距离左边框 10 像素
                left_y_offset = 0  # 距离顶部 10 像素

                # 右侧头像位置
                right_x_offset = 774  # 距离右边框 10 像素
                right_y_offset = 0  # 距离顶部 10 像素

                # logo 位置
                logo_x_offset = 750
                logo_y_offset = 480

                # 创建视频输出对象
                fourcc = cv2.VideoWriter_fourcc(*'mp4v')  # 编码格式
                output_dir = os.path.join(
                    self.single_video_path, task["session_code"][:10], task["session_code"][:-2])
                if not os.path.exists(output_dir):
                    os.makedirs(output_dir)
                output_video_path = os.path.join(output_dir, item['video_name'])
                item['video_name'] = output_video_path
                out = cv2.VideoWriter(
                    output_video_path, fourcc, fps, (frame_width, frame_height))

                # 使用 Pillow 渲染中文文本
                font_path = resource_path("SourceHanSansCN-Regular.ttf")  # 你需要提供一个支持中文的字体文件路径
                font_size = 25
                for index, value in enumerate(item['scores']):
                    if item['scores'][index]['frame_id'] == item['start_frame']:
                        item['scores'][index]['frame_id'] = item['start_frame'] - 25
                    if item['player'] == 0:
                        image = Image.open(resource_path("image_0.png")).convert(
                            "RGBA")  # 打开图片并转换为 RGBA 模式（包括 alpha 通道）
                    else:
                        image = Image.open(resource_path("image_1.png")).convert(
                            "RGBA")  # 打开图片并转换为 RGBA 模式（包括 alpha 通道）
                    # 创建一个 ImageDraw 对象
                    draw_ = ImageDraw.Draw(image)

                    # 加载字体
                    font_0 = ImageFont.truetype(
                        resource_path("SourceHanSansCN-Regular.ttf"), 16)
                    font_1 = ImageFont.truetype(
                        resource_path("PreferredTitleBlack.ttf"), 28)

                    # 在图像上绘制中文文本
                    draw_.text((190, 5), user_0, font=font_0, fill=(46, 107, 229))    # 蓝色-甲用户名
                    draw_.text((275 + 190, 5), task['format'], font=font_0, fill="white")   # 白色
                    draw_.text((510 + 215, 5), user_1, font=font_0, fill=(46, 107, 229))   # 蓝色-乙用户名
                    # 将 Pillow 图像转换为 OpenCV 格式
                    current_score = item['scores'][index]
                    total_score = str(current_score['score'])
                    score_length = len(total_score)
                    if score_length == 1:
                        draw_.text((388 + 190, -1), str(current_score['score_1']), font=font_1, fill=(46, 107, 229))  # 乙得分
                        draw_.text((174 + 190, -1), str(current_score['score_0']), font=font_1, fill=(46, 107, 229))  # 甲得分
                    elif score_length == 2:
                        draw_.text((378 + 190, -1), str(current_score['score_1']), font=font_1, fill=(46, 107, 229))  # 乙得分
                        draw_.text((168 + 190, -1), str(current_score['score_0']), font=font_1, fill=(46, 107, 229))  # 甲得分
                    else:
                        draw_.text((372 + 190, -1), str(current_score['score_1']), font=font_1, fill=(46, 107, 229))  # 乙得分
                        draw_.text((164 + 190, -1), str(current_score['score_0']), font=font_1, fill=(46, 107, 229))  # 甲得分

                    result = re.findall(r'[\u4e00-\u9fa5]+|\d+|\w+', text)
                    if item['player'] == 0:
                        # 在图像上绘制文本
                        draw_.text((5, -1), result[0], font=font_1, fill="#1f4ca3")  # 蓝色--'单杆'

                        # 在图像上绘制文本
                        draw_.text((63, -1), result[1], font=font_1, fill="#f50d19")   # 红色

                        # 在图像上绘制文本
                        if len(result[1]) == 1:    # '分'
                            draw_.text((90, -1), result[2], font=font_1, fill="#1f4ca3")
                        elif len(result[1]) == 2:
                            draw_.text((110, -1), result[2], font=font_1, fill="#1f4ca3")
                        else:
                            draw_.text((128, -1), result[2], font=font_1, fill="#1f4ca3")

                    else:
                        # 在图像上绘制文本
                        draw_.text((820, -1), result[0], font=font_1, fill="#1f4ca3")  # 蓝色--'单杆'

                        # 在图像上绘制文本
                        draw_.text((875, -1), result[1], font=font_1, fill="#f50d19")  # 红色

                        # 在图像上绘制文本
                        if len(result[1]) == 1:  # '分'
                            draw_.text((897, -1), result[2], font=font_1, fill="#1f4ca3")
                        elif len(result[1]) == 2:
                            draw_.text((915, -1), result[2], font=font_1, fill="#1f4ca3")
                        else:
                            draw_.text((935, -1), result[2], font=font_1, fill="#1f4ca3")

                    if item['player'] == 1:   # 单杆得分
                        draw_.text((461 + 180, 3), str(item['scores'][index]['score']), font=font_0, fill="#f50d19")
                    else:
                        if score_length == 1:
                            draw_.text((118 + 195, 3), str(item['scores'][index]['score']), font=font_0, fill="#f50d19")
                        elif score_length == 2:
                            draw_.text((109 + 195, 3), str(item['scores'][index]['score']), font=font_0, fill="#f50d19")
                        else:
                            draw_.text((100 + 195, 3), str(item['scores'][index]['score']), font=font_0, fill="#f50d19")

                    opencv_image = np.array(image)

                    # 转换为 BGR 格式（OpenCV 默认使用 BGR）
                    main_img = cv2.cvtColor(opencv_image, cv2.COLOR_RGBA2BGRA)

                    if main_img is None:
                        logger.error("无法打开主图片文件")
                        return False
                    # 调整主图片大小
                    main_h, main_w, main_channels = main_img.shape
                    # 计算主图片顶部位置
                    main_x_offset = (frame_width - main_w) // 2  # 横向居中
                    main_y_offset = 0  # 顶部对齐

                    while True:
                        ret, frame = cap.read()
                        if not ret:
                            break
                        frame_count += 1
                        frame_with_text = frame.copy()

                        if frame_count > (
                                item['end_frame'] if index + 1 == len(item['scores']) else item['scores'][index + 1][
                                    'frame_id']):
                            break

                        if value['frame_id'] < frame_count <= (
                                item['end_frame'] if index + 1 == len(item['scores']) else item['scores'][index + 1][
                                    'frame_id']):
                            # 将 OpenCV 图像转换为 PIL 图像
                            pil_image = Image.fromarray(
                                cv2.cvtColor(frame_with_text, cv2.COLOR_BGR2RGB))
                            # 将 PIL 图像转换回 OpenCV 图像
                            frame_with_text = cv2.cvtColor(
                                np.array(pil_image), cv2.COLOR_RGB2BGR)

                            # 叠加主图片（顶部居中）
                            y1, y2 = main_y_offset, main_y_offset + main_h
                            x1, x2 = main_x_offset, main_x_offset + main_w
                            if main_channels == 4:  # 主图片有 alpha 通道
                                alpha_main = main_img[:, :, 3] / 255.0
                                alpha_frame = 1.0 - alpha_main
                                for c in range(0, 3):
                                    frame_with_text[y1:y2, x1:x2, c] = (
                                            alpha_main * main_img[:, :, c] +
                                            alpha_frame * frame_with_text[y1:y2, x1:x2, c]
                                    )
                            else:
                                frame_with_text[y1:y2, x1:x2] = main_img

                            # 叠加左侧头像
                            y1, y2 = left_y_offset, left_y_offset + avatar_h
                            x1, x2 = left_x_offset, left_x_offset + avatar_w
                            if avatar_channels == 4:  # 左侧头像有 alpha 通道
                                alpha_left = left_avatar[:, :, 3] / 255.0
                                alpha_frame = 1.0 - alpha_left
                                for c in range(0, 3):
                                    frame_with_text[y1:y2, x1:x2, c] = (
                                            alpha_left * left_avatar[:, :, c] +
                                            alpha_frame * frame_with_text[y1:y2, x1:x2, c]
                                    )
                            else:
                                frame_with_text[y1:y2, x1:x2] = left_avatar

                            # 叠加右侧头像
                            y1, y2 = right_y_offset, right_y_offset + avatar_h
                            x1, x2 = right_x_offset, right_x_offset + avatar_w
                            if avatar_channels == 4:  # 右侧头像有 alpha 通道
                                alpha_right = right_avatar[:, :, 3] / 255.0
                                alpha_frame = 1.0 - alpha_right
                                for c in range(0, 3):
                                    frame_with_text[y1:y2, x1:x2, c] = (
                                            alpha_right * right_avatar[:, :, c] +
                                            alpha_frame * frame_with_text[y1:y2, x1:x2, c]
                                    )
                            else:
                                frame_with_text[y1:y2, x1:x2] = right_avatar

                            # 叠加logo
                            y1, y2 = logo_y_offset, logo_y_offset + logo_h
                            x1, x2 = logo_x_offset, logo_x_offset + logo_w

                            if logo_channels == 4:  # 右侧头像有 alpha 通道
                                logo_right = logo[:, :, 3] / 255.0
                                logo_frame = 1.0 - logo_right
                                for c in range(0, 3):
                                    frame_with_text[y1:y2, x1:x2, c] = (
                                            logo_right * logo[:, :, c] +
                                            logo_frame * frame_with_text[y1:y2, x1:x2, c]
                                    )
                            else:
                                frame_with_text[y1:y2, x1:x2] = logo
                            out.write(frame_with_text)
                        else:
                            continue
                out.release()
            cap.release()
            cv2.destroyAllWindows()
            return True
        except Exception as e:
            logger.error("single_shot_video failed,{}".format(e))
            return False
