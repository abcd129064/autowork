# -*- coding: utf-8 -*-
"""斯诺克视频日志全自动整理工具（Excel 驱动）

按当天 Excel（问题/未复现/精度 三个 sheet）中的署名筛选记录，逐条把
视频、同名 log、daily 日志、detect.bin、日期文件夹从 videos/{球房}
归档到输出目录下的「球房 and 球房」总文件夹。可独立 CLI 运行
（仓库根下 `python -m windows.tools.newlog`，需项目根在 sys.path 上
才能读到 core.app_settings 配置），也被 NewLogWorker 后台线程收编调用
（传 target_name）。

全程用 print 输出进度，是因为 GUI 侧把 worker 的 stdout 重定向到日志框；
改成 logging 会静默丢掉这些进度。归档只复制不移动，原始目录保持完整、可重跑。
"""
import os
import sys
import json
import shutil
import openpyxl
from datetime import datetime, timedelta

# 日志内时间戳精确到微秒；只认这一种格式，解析不出按「该行无时间戳」处理
TIME_FORMAT = "%Y-%m-%d %H:%M:%S.%f"


# GUI 收编默认署名（与历史行为一致；GUI 调用方通过 target_name 参数覆盖）
DEFAULT_TARGET_NAME = "沈喆"

# 路径默认值（settings.json 缺失时回退到当前用户桌面）
_DEFAULT_VIDEOS_DIR = os.path.expanduser(r"~\Desktop\videos")
_DEFAULT_EXCEL_DIR = os.path.expanduser(r"~\Desktop\excel")
_DEFAULT_OUT_DIR = os.path.expanduser(r"~\Desktop")


def _load_settings() -> dict:
    """读取配置门面合并视图（敏感字段透明解密），失败时返回空配置。

    配置读不到只该让各处路径回退到桌面默认值，不该让整个工具不可用；
    core.app_settings 会连带引入 Qt 依赖，独立 CLI 环境下导入失败也走这条兜底。
    """
    try:
        from core import app_settings
        return app_settings.get_merged()
    except Exception:
        # 空 dict 让下面每个 get 都落到默认路径，比抛异常中断整理更可接受
        return {}


def parse_time_from_line(line):
    """解析日志行开头的 [时间戳] 前缀，取不到合法时间就返回 None。

    日志里混着启动横幅、分隔线这类无时间戳的行，调用方靠 None 判断该行是否
    参与时间窗口过滤；这里刻意用裸 except 把所有解析异常统一归成 None。
    """
    try:
        # 取首个 [ 与首个 ]：时间戳固定行首，行内其它方括号不参与解析
        left = line.find('[')
        right = line.find(']')
        if left == -1 or right == -1:
            return None
        time_str = line[left + 1: right].strip()
        return datetime.strptime(time_str, TIME_FORMAT)
    except:
        return None


def get_log_start_end_time(log_path):
    """取日志首/末行时间戳作为时间范围，任一端取不到就返回 None。

    日志只追加不重排，首末行就是最早/最晚时间，不必全文件求极值。设备侧日志
    是 GBK 且可能混入非法字节，errors="ignore" 跳过坏字节；否则一个坏字符就
    会让整份日志读不出来，连带丢掉本来可用的时间范围。
    """
    try:
        with open(log_path, "r", encoding="gbk", errors="ignore") as f:
            lines = [line.strip() for line in f if line.strip()]
        # 空文件（设备刚建日志还没写入）视为没有时间范围
        if not lines:
            return None, None
        start_t = parse_time_from_line(lines[0])
        end_t = parse_time_from_line(lines[-1])
        return start_t, end_t
    except:
        # 文件被占用或权限不足时按「无时间范围」处理，不牵连其它归档步骤
        return None, None


def filter_daily_log(daily_path, expand_start, expand_end, save_path):
    """按时间窗口截取 daily 日志行另存为 UTF-8，返回是否写成功。

    用 utf-8-sig 带 BOM，是为了让人直接在 Excel 里打开时中文不乱码；不带 BOM
    的 UTF-8 会被 Excel 按本地编码误读。逐行处理而非整读，是因为 daily 日志
    动辄几十万行，一次性读进内存没有必要。
    """
    try:
        result = []
        with open(daily_path, "r", encoding="gbk", errors="ignore") as f:
            for line in f:
                try:
                    line = line.strip()
                    if not line:
                        continue
                    curr_t = parse_time_from_line(line)
                    # 无时间戳的行一律丢弃：判断不了它属于哪个时间窗口
                    if curr_t and expand_start <= curr_t <= expand_end:
                        result.append(line)
                except:
                    continue
        # 一条都没命中也要落空文件：下游以「文件已生成」作为这一步完成的标志
        with open(save_path, "w", encoding="utf-8-sig") as f:
            f.write("\n".join(result))
        return True
    except:
        # 目标被占用或磁盘满都只返回 False，让调用方保留现场而不是抛出中断
        return False


def read_excel_data(excel_path, target_name=DEFAULT_TARGET_NAME):
    """按署名从三个 sheet 提取 (球房, 视频名)，返回 (记录列表, 球房集合)。

    data_only=True 取公式的计算结果而非公式字符串：台账里的统计列常写成公式，
    不带这个参数会读到 "=..." 文本。表头按列名定位而不是写死列号，因为模板
    列顺序调整过多次，写死会在换模板后静默取错列。缺署名/球房/视频名任一列
    的 sheet 直接跳过，不去猜列位置。
    """
    result = []
    # 球房去重后用来拼总文件夹名，同一球房的多条记录只算一次
    all_rooms = set()
    try:
        wb = openpyxl.load_workbook(excel_path, data_only=True)
        # 三个 sheet 是业务固定分类，写死名字；表名改了要同步改这里，不做模糊匹配
        sheets_to_read = ["问题", "未复现", "精度"]

        for sheet_name in sheets_to_read:
            if sheet_name not in wb.sheetnames:
                # 缺 sheet 只提示不失败：不同日期的台账可能只建了其中几类
                print(f"  ⚠️ 工作表 '{sheet_name}' 不存在，跳过")
                continue

            ws = wb[sheet_name]

            # 表头只扫第一行：模板约定表头固定在第 1 行，向下扫描会把数据值当列名
            headers = {}
            for col_idx, cell in enumerate(ws[1], 1):
                if cell.value:
                    headers[cell.value.strip()] = col_idx

            # 列名按原样比对（去首尾空格），模板把「署名」改成「写名人」之类就必须同步改代码
            if "署名" not in headers or "球房" not in headers or "视频名" not in headers:
                print(f"  ⚠️ 工作表 '{sheet_name}' 缺少必要的列（署名/球房/视频名），跳过")
                continue

            sig_col = headers["署名"]
            room_col = headers["球房"]
            video_col = headers["视频名"]

            for row_idx in range(2, ws.max_row + 1):
                try:
                    sig_cell = ws.cell(row_idx, sig_col).value
                    # 署名精确匹配：包含匹配会把「沈喆」和「沈喆1」的记录混到一起
                    if sig_cell and sig_cell.strip() == target_name:
                        room_val = ws.cell(row_idx, room_col).value
                        video_val = ws.cell(row_idx, video_col).value

                        # 空球房/空视频名定位不到源文件，跳过该行而不是拼出错误路径
                        if not room_val or not video_val:
                            continue

                        room = str(room_val).strip()
                        video = str(video_val).strip()
                        result.append((room, video))
                        all_rooms.add(room)
                        print(f"  📋 从 '{sheet_name}' 提取: 球房={room}, 视频名={video}")
                except Exception as e:
                    # 单行脏数据（合并单元格取到 None 等）只跳过本行，不连累整表提取
                    continue

        # 显式关闭释放句柄：Windows 下不关会让下次重跑读同一文件时报占用
        wb.close()
        print(f"\n✅ 共提取 {len(result)} 条记录（署名：{target_name}）")
        print(f"✅ 涉及球桌: {', '.join(sorted(all_rooms))}")
        return result, all_rooms

    except Exception as e:
        # 工作簿整体打不开（被 Excel 占用、文件损坏）时返回空结果，由 main 统一提示
        print(f"❌ 读取Excel失败: {e}")
        return [], set()


def get_video_date_from_name(video_name):
    """把视频名前 8 位数字日期转成 YYYY-MM-DD，格式不符返回 None。

    视频名以 YYYYMMDD_ 开头是设备命名约定。不用文件 mtime 推日期：拷贝、移动
    都会改写 mtime，文件名里的日期才是设备真正录制那天。解析不出日期时只跳过
    daily 日志与日期文件夹，视频本身仍照常归档。
    """
    try:
        date_part = video_name.split('_')[0]
        if len(date_part) == 8 and date_part.isdigit():
            year = date_part[0:4]
            month = date_part[4:6]
            day = date_part[6:8]
            return f"{year}-{month}-{day}"
    except:
        pass
    return None


def find_daily_log(table_path, date_str):
    """在球桌目录找以 daily 开头且文件名含指定日期的日志，找不到返回 None。

    daily 日志落在球桌目录根下、按日期命名，不与视频同名，只能按日期反查。
    前缀比较前转小写，避免 Daily、DAILY 这类大小写变体漏找；目录不可读时
    同样返回 None，由调用方按「日志缺失」继续归档。
    """
    try:
        for f in os.listdir(table_path):
            if f.lower().startswith("daily") and date_str in f:
                return os.path.join(table_path, f)
    except:
        pass
    return None


def find_log_file(table_path, video_name):
    """找与视频同名的 .log 文件，不存在返回 None。

    录像程序把 log 写在视频旁边并用视频名命名，所以只做同名探测；不做模糊
    匹配，避免把别的场次日志一起带进归档目录。
    """
    try:
        log_file = f"{video_name}.log"
        log_path = os.path.join(table_path, log_file)
        if os.path.exists(log_path):
            return log_path
    except:
        # 非法路径或权限问题一律按「不存在」处理，缺 log 不算归档失败
        pass
    return None


def get_today_excel_path():
    """当天 Excel 路径：{excel_dir}/{YYYY MM DD}.xlsx。

    文件名用空格分隔日期（"2026 07 29.xlsx"），与台账模板的命名约定一致，改成
    短横线就读不到当天的表。每次调用都重读配置，设置页改完目录立刻生效。
    """
    today = datetime.now()
    # 文件名用空格分隔（"2026 07 29.xlsx"），与台账模板约定一致
    date_str = today.strftime("%Y %m %d")
    excel_filename = f"{date_str}.xlsx"
    # Excel 目录来自 settings.json 的 newlog_excel_dir，缺失时回退桌面默认值
    excel_dir = str(_load_settings().get("newlog_excel_dir") or _DEFAULT_EXCEL_DIR)
    return os.path.join(excel_dir, excel_filename)


def main(target_name=None, interactive=False):
    """整理主流程：读 Excel、逐条归档、汇总打印，返回输出总目录。

    单条记录失败只累计 fail_count 并继续下一条：批量整理时一条视频缺失不该
    让整批中断。interactive=True 供独立 CLI 使用，出错与结束时等待回车，否则
    双击运行的命令行窗口会一闪而过，看不到失败原因。
    """
    print("==================================================")
    print("     斯诺克视频日志自动整理工具（Excel驱动版）")
    print("        根据Excel署名筛选，自动归档")
    print("==================================================\n")

    # GUI 收编（NewLogWorker 后台线程）调用时传入 target_name；
    # 独立 CLI 运行时使用默认署名
    # 空串或全空格都回退默认署名：GUI 传空值时不能把关照过滤成空字符串
    target_name = (str(target_name or "").strip() or DEFAULT_TARGET_NAME)
    
    # 配置路径（settings.json 驱动：videos_dir / newlog_excel_dir / newlog_out_dir）
    settings = _load_settings()
    work_root = str(settings.get("videos_dir") or _DEFAULT_VIDEOS_DIR)
    out_save_dir = str(settings.get("newlog_out_dir") or _DEFAULT_OUT_DIR)

    # 自动获取当天日期的Excel文件路径
    excel_path = get_today_excel_path()

    # 检查Excel文件是否存在
    if not os.path.exists(excel_path):
        print(f"❌ Excel文件不存在: {excel_path}")
        print(f"   请确保今天的Excel文件已放置在: {os.path.dirname(excel_path)}")
        print(f"   文件名格式应为: {datetime.now().strftime('%Y %m %d')}.xlsx")
        if interactive:
            input("\n按下回车键退出...")
        return

    print(f"📄 使用Excel文件: {os.path.basename(excel_path)}")
    print("📖 正在读取Excel文件...")
    # 读取失败与「当天没有本人记录」都返回空列表，这里统一按无数据处理
    records, all_rooms = read_excel_data(excel_path, target_name=target_name)

    if not records:
        print(f"❌ 未找到署名'{target_name}'的数据，程序退出。")
        if interactive:
            input("按下回车键退出...")
        return

    # 创建总文件夹，名字为所有球桌号用 " and " 连接
    # 排序后拼接：set 遍历顺序不稳定，不排序会让同一批球房反复生成不同名目录
    folder_name = " and ".join(sorted(all_rooms))
    main_out_path = os.path.join(out_save_dir, folder_name)

    if not os.path.exists(main_out_path):
        os.makedirs(main_out_path)
        print(f"\n📁 创建总文件夹: {folder_name}")

    print(f"\n🚀 开始处理 {len(records)} 条记录...\n")

    # 失败清单不单独落盘，这两个计数是用户唯一能看到的整体结果
    success_count = 0
    fail_count = 0

    # 逐条处理且各自兜底：已完成的文件留在输出目录，重跑只覆盖同名文件
    for idx, (room, video_name) in enumerate(records, 1):
        print(f"\n[{idx}/{len(records)}] 处理: 球房={room}, 视频名={video_name}")

        try:
            # 1. 构建球桌路径
            table_path = os.path.join(work_root, room)
            # 球桌目录不存在通常是 videos_dir 配错，或 Excel 里的球房号写法不一致
            if not os.path.exists(table_path):
                print(f"  ❌ 球桌文件夹不存在: {table_path}")
                fail_count += 1
                continue

            # 2. 在总文件夹下创建球桌子文件夹
            # 输出侧按球房再分一层，与源目录结构一致，方便整桌打包发人
            table_out_path = os.path.join(main_out_path, room)
            if not os.path.exists(table_out_path):
                os.makedirs(table_out_path)
                print(f"  📁 创建子文件夹: {room}")

            # 3. 查找视频文件（在根目录）
            video_file = f"{video_name}.mp4"
            src_video = os.path.join(table_path, video_file)

            if not os.path.exists(src_video):
                # 视频缺失直接判失败：没有视频的归档没有意义，不为它建半成品目录
                print(f"  ❌ 视频文件不存在: {video_file}")
                fail_count += 1
                continue

            # 4. 复制视频文件
            # copy2 保留修改时间：复盘时要靠文件时间把视频画面与日志时间轴对上
            dst_video = os.path.join(table_out_path, video_file)
            shutil.copy2(src_video, dst_video)
            print(f"  ✅ 视频复制完成: {video_file}")

            # 5. 查找并复制log文件
            # 日志与视频同名只差扩展名，直接按名拼接，不做目录扫描
            src_log = find_log_file(table_path, video_name)
            if src_log:
                dst_log = os.path.join(table_out_path, f"{video_name}.log")
                shutil.copy2(src_log, dst_log)
                print(f"  ✅ log复制完成: {video_name}.log")
            else:
                # log 缺失不判失败：老录像可能只落了视频，没有同名日志
                print(f"  ⚠️ log文件不存在，跳过")

            # 6. 从视频名提取日期，查找并复制daily日志
            # daily 日志按日期命名、与视频不同名，只能靠视频名里的日期反查
            date_str = get_video_date_from_name(video_name)
            if date_str:
                daily_log = find_daily_log(table_path, date_str)
                if daily_log and os.path.exists(daily_log):
                    dst_daily = os.path.join(table_out_path, os.path.basename(daily_log))
                    shutil.copy2(daily_log, dst_daily)
                    print(f"  ✅ daily日志复制完成: {os.path.basename(daily_log)}")
                else:
                    print(f"  ⚠️ daily日志不存在，跳过")
            else:
                print(f"  ⚠️ 无法从视频名提取日期，跳过daily日志")

            # 7. 复制detect.bin
            # 标定文件整个球桌共用一份、不分日期，缺了只能回源头重新标定
            src_bin = os.path.join(table_path, "detect.bin")
            if os.path.exists(src_bin):
                dst_bin = os.path.join(table_out_path, "detect.bin")
                shutil.copy2(src_bin, dst_bin)
                print(f"  ✅ detect.bin复制完成")
            else:
                print(f"  ⚠️ detect.bin不存在，跳过")

            # 8. 查找并复制日期文件夹（如果存在）
            if date_str:
                # 日期目录有两种命名（带不带连字符），命中第一个就 break，避免重复拷贝
                possible_date_folders = [date_str, date_str.replace("-", "")]
                # 只认这两种命名，其它名字的目录不猜，避免把无关目录整包拷走
                for date_folder_name in possible_date_folders:
                    src_date_folder = os.path.join(table_path, date_folder_name)
                    if os.path.exists(src_date_folder) and os.path.isdir(src_date_folder):
                        dst_date_folder = os.path.join(table_out_path, date_folder_name)
                        # 目标已存在时 copytree 会直接抛错，先清掉再复制保证可重跑
                        if os.path.exists(dst_date_folder):
                            shutil.rmtree(dst_date_folder)
                        # copytree 递归整目录复制，日期目录内的分层结构原样保留
                        shutil.copytree(src_date_folder, dst_date_folder)
                        print(f"  ✅ 日期文件夹复制完成: {date_folder_name}")
                        break
                else:
                    print(f"  ⚠️ 日期文件夹不存在，跳过")

            success_count += 1
            print(f"  ✅ 处理完成!")

        except Exception as e:
            # 单条记录的异常只记账不中断：一条坏数据不该拖住整批归档
            print(f"  ❌ 处理异常: {str(e)}")
            fail_count += 1
            continue

    print("\n" + "=" * 50)
    print(f"📊 处理完成！")
    print(f"   ✅ 成功: {success_count} 条")
    print(f"   ❌ 失败: {fail_count} 条")
    print(f"   📁 输出目录: {main_out_path}")
    print("=" * 50)

    if interactive:
        input("\n按下回车键关闭工具...")

    # 返回总目录给 GUI 侧「打开输出目录」用，CLI 调用方可以忽略
    return main_out_path


# 独立运行入口；GUI 集成时由 NewLogWorker 直接调 main()，不走这里
if __name__ == "__main__":
    main(interactive=True)