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
    """单杆视频渲染器：按 json 任务把比分条/头像/logo 逐帧叠加到原始视频上"""

    def __init__(self, source_dir, single_video_path, processed_dir, abnormal_dir):
        self.source_dir = source_dir  # 监控目录
        self.processed_dir = processed_dir  # 已处理目录
        self.abnormal_dir = abnormal_dir  # 异常目录
        self.single_video_path = single_video_path  # 单杆路径

    def move_to_processed(self, file_path, type):
        """把已处理文件移到 processed_dir，同名自动加 _1/_2 后缀"""
        try:
            if type == 1:
                # 目标路径已存在时自动添加 `_1`, `_2` 后缀
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

    def process_folder(self, folder_path):
        """处理文件夹中全部 json 任务：读 json → 生成视频 → 删 json"""
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
                os.remove(json_path)
            except json.JSONDecodeError as e:
                logger.error(f"JSON 解析失败: {e}")
            except FileNotFoundError:
                logger.error("文件不存在，请检查路径")
        return True

    def monitor_directory(self, target_folder=None):
        """单次处理模式：只制作目标文件夹（缺省扫 source_dir 下含 json 的
        二级子目录，兼容 日期/代码 两级结构），处理完即退出不轮询"""
        if target_folder is not None:
            # 只制作指定文件夹
            self.process_folder(target_folder)
            return

        # 单次扫描：只处理含 json 的文件夹
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
        """加载头像/logo：http 地址下载解码，失败回退内置头像；
        本地路径不存在且非绝对路径时先试 resource 目录，避免 imread 盲读告警"""
        import requests
        if image_url[:4] == "http":
            response = requests.get(image_url)

            if response.status_code == 200:
                img_array = np.frombuffer(response.content, np.uint8)
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
            # 非 http 路径：优先用存在的文件，相对路径找不到时回退 resource 目录
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
        """按单个 json 任务渲染视频：每段 scores 区间叠比分条，返回是否成功"""
        import os
        try:
            frame_count = 0
            videos = task['videos']
            file_name = task['file_name']
            session_code = task['session_code']

            # 逐段视频：末尾补一条 +25 帧的同分记录，让最后一段比分条渲染到片段结束
            # 与下方 start_frame - 25 配对，首尾各延伸一截，
            # 否则尾段区间覆盖不完整，比分条会在片段结束前消失
            for video in videos:
                scores = video["scores"]
                if scores:
                    last_score = scores[-1]
                    new_score = last_score.copy()
                    new_score["frame_id"] += 25
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

                # 头像/logo 统一转 4 通道（灰度先转 RGB），后续按 alpha 叠加
                if len(left_avatar.shape) == 2:
                    left_avatar = cv2.cvtColor(left_avatar, cv2.COLOR_GRAY2RGB)
                if left_avatar.shape[2] == 3:
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

                # 头像/logo 叠放坐标（比分条底图上的固定位）
                left_x_offset = 154
                left_y_offset = 0
                right_x_offset = 774
                right_y_offset = 0
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

                # Pillow 渲染中文比分条（cv2.putText 不支持中文），字体随包分发
                font_path = resource_path("SourceHanSansCN-Regular.ttf")
                font_size = 25
                for index, value in enumerate(item['scores']):
                    # 首段比分条起点前移 25 帧（与末尾 +25 对称）；算出负值也安全：
                    # 下方区间判断里 frame_count 从 1 起算，条件天然成立，比分条从第一帧就出现
                    if item['scores'][index]['frame_id'] == item['start_frame']:
                        item['scores'][index]['frame_id'] = item['start_frame'] - 25
                    # 底图按选手方向选（image_0/image_1），转 RGBA 供绘制
                    if item['player'] == 0:
                        image = Image.open(resource_path("image_0.png")).convert("RGBA")
                    else:
                        image = Image.open(resource_path("image_1.png")).convert("RGBA")
                    draw_ = ImageDraw.Draw(image)

                    font_0 = ImageFont.truetype(
                        resource_path("SourceHanSansCN-Regular.ttf"), 16)
                    font_1 = ImageFont.truetype(
                        resource_path("PreferredTitleBlack.ttf"), 28)

                    # 用户名/赛制文字（坐标为底图模板上的固定位）
                    draw_.text((190, 5), user_0, font=font_0, fill=(46, 107, 229))    # 蓝色-甲用户名
                    draw_.text((275 + 190, 5), task['format'], font=font_0, fill="white")   # 白色
                    draw_.text((510 + 215, 5), user_1, font=font_0, fill=(46, 107, 229))   # 蓝色-乙用户名
                    current_score = item['scores'][index]
                    total_score = str(current_score['score'])
                    score_length = len(total_score)
                    # 总比分按位数微调 x 坐标，保证居中于底图记分槽
                    if score_length == 1:
                        draw_.text((388 + 190, -1), str(current_score['score_1']), font=font_1, fill=(46, 107, 229))  # 乙得分
                        draw_.text((174 + 190, -1), str(current_score['score_0']), font=font_1, fill=(46, 107, 229))  # 甲得分
                    elif score_length == 2:
                        draw_.text((378 + 190, -1), str(current_score['score_1']), font=font_1, fill=(46, 107, 229))  # 乙得分
                        draw_.text((168 + 190, -1), str(current_score['score_0']), font=font_1, fill=(46, 107, 229))  # 甲得分
                    else:
                        draw_.text((372 + 190, -1), str(current_score['score_1']), font=font_1, fill=(46, 107, 229))  # 乙得分
                        draw_.text((164 + 190, -1), str(current_score['score_0']), font=font_1, fill=(46, 107, 229))  # 甲得分

                    # 「单杆N分」拆成 中文/数字/单位 三段分别上色，数字位数决定偏移
                    result = re.findall(r'[\u4e00-\u9fa5]+|\d+|\w+', text)
                    if item['player'] == 0:
                        draw_.text((5, -1), result[0], font=font_1, fill="#1f4ca3")  # 蓝色--'单杆'
                        draw_.text((63, -1), result[1], font=font_1, fill="#f50d19")   # 红色
                        if len(result[1]) == 1:    # '分'
                            draw_.text((90, -1), result[2], font=font_1, fill="#1f4ca3")
                        elif len(result[1]) == 2:
                            draw_.text((110, -1), result[2], font=font_1, fill="#1f4ca3")
                        else:
                            draw_.text((128, -1), result[2], font=font_1, fill="#1f4ca3")

                    else:
                        draw_.text((820, -1), result[0], font=font_1, fill="#1f4ca3")  # 蓝色--'单杆'
                        draw_.text((875, -1), result[1], font=font_1, fill="#f50d19")  # 红色
                        if len(result[1]) == 1:  # '分'
                            draw_.text((897, -1), result[2], font=font_1, fill="#1f4ca3")
                        elif len(result[1]) == 2:
                            draw_.text((915, -1), result[2], font=font_1, fill="#1f4ca3")
                        else:
                            draw_.text((935, -1), result[2], font=font_1, fill="#1f4ca3")

                    # 本杆得分（小字号，位置同样按位数微调）
                    if item['player'] == 1:
                        draw_.text((461 + 180, 3), str(item['scores'][index]['score']), font=font_0, fill="#f50d19")
                    else:
                        if score_length == 1:
                            draw_.text((118 + 195, 3), str(item['scores'][index]['score']), font=font_0, fill="#f50d19")
                        elif score_length == 2:
                            draw_.text((109 + 195, 3), str(item['scores'][index]['score']), font=font_0, fill="#f50d19")
                        else:
                            draw_.text((100 + 195, 3), str(item['scores'][index]['score']), font=font_0, fill="#f50d19")

                    opencv_image = np.array(image)

                    # Pillow 输出是 RGBA，转 BGRA 供 OpenCV 叠加
                    # 因为 OpenCV 是 BGR 通道序，不转换直接叠加会红蓝互换
                    main_img = cv2.cvtColor(opencv_image, cv2.COLOR_RGBA2BGRA)

                    if main_img is None:
                        logger.error("无法打开主图片文件")
                        return False
                    main_h, main_w, main_channels = main_img.shape
                    main_x_offset = (frame_width - main_w) // 2  # 比分条横向居中、顶部对齐
                    main_y_offset = 0

                    # 逐帧输出：当前分数区间内的帧叠加比分条/头像/logo，
                    # 越过本段结束帧（下一段 frame_id 或 end_frame）即换下一张比分条
                    while True:
                        ret, frame = cap.read()
                        if not ret:
                            break
                        frame_count += 1
                        frame_with_text = frame.copy()

                        # 已越过本段结束帧：跳出让外层换下一张比分条。
                        # 注意：越界时刚读入的这一帧（boundary+1）已被 cap 消费但不会写出，
                        # 每个段位切换点会丢 1 帧；若要求无损输出需改为把这帧留给下一段处理
                        if frame_count > (
                                item['end_frame'] if index + 1 == len(item['scores']) else item['scores'][index + 1][
                                    'frame_id']):
                            break

                        if value['frame_id'] < frame_count <= (
                                item['end_frame'] if index + 1 == len(item['scores']) else item['scores'][index + 1][
                                    'frame_id']):
                            # 帧转 PIL 再转回（历史保留路径，保持与模板渲染管线一致）
                            pil_image = Image.fromarray(
                                cv2.cvtColor(frame_with_text, cv2.COLOR_BGR2RGB))
                            frame_with_text = cv2.cvtColor(
                                np.array(pil_image), cv2.COLOR_RGB2BGR)

                            # 叠加比分条（顶部居中）
                            y1, y2 = main_y_offset, main_y_offset + main_h
                            x1, x2 = main_x_offset, main_x_offset + main_w
                            if main_channels == 4:  # 带 alpha 通道按透明度混合
                                # alpha 混合三步（下方两个头像/logo 同一套算法）：
                                # 1. 前景 alpha 归一到 0~1；2. 背景权重 = 1 - 前景 alpha
                                # 3. 逐通道加权和：alpha=1 完全盖住原帧，=0 完全透出
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
                            if avatar_channels == 4:
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
                            if avatar_channels == 4:
                                alpha_right = right_avatar[:, :, 3] / 255.0
                                alpha_frame = 1.0 - alpha_right
                                for c in range(0, 3):
                                    frame_with_text[y1:y2, x1:x2, c] = (
                                            alpha_right * right_avatar[:, :, c] +
                                            alpha_frame * frame_with_text[y1:y2, x1:x2, c]
                                    )
                            else:
                                frame_with_text[y1:y2, x1:x2] = right_avatar

                            # 叠加 logo
                            y1, y2 = logo_y_offset, logo_y_offset + logo_h
                            x1, x2 = logo_x_offset, logo_x_offset + logo_w

                            if logo_channels == 4:
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
