# -*- coding: utf-8 -*-
r"""企微售后群聊天记录采集冒烟（2026-10-06）

真机独占桌面：激活并最大化企业微信 → 拖选消息 →（PageUp+微移 或 滚轮）
逐屏 Ctrl+C → 剪贴板文本落盘 → core.wecom_clip 解析去重 → 打印批次连续性自检。
意义是"没有企微超管权限、开不了官方会话存档"时仍能补历史，采集机制与投入产出
见 docs/企微售后群消息归档-剪贴板采集器设计与落地2026-10-06.md。

**必须脱离文件沙箱运行**：受限令牌下 SetCursorPos/SendInput 会被静默忽略
（SendInput 返回成功但光标不动），表现为"点不动鼠标"而不是报错。

用法（唯一 GUI/采集基线解释器）：
    # ① 只读自检：结构体/坐标/剪贴板/解析器，不发任何输入事件
    E:\ANACONDA\python.exe tools/smoke/smoke_wecom_clip_collect.py --selftest
    # ② 只读侦察：列企微窗口与当前鼠标位置
    E:\ANACONDA\python.exe tools/smoke/smoke_wecom_clip_collect.py --probe
    # ③ 真采（在群里滚到最新一条，蜂鸣后 12 秒内把鼠标停到消息列表底部中央）
    E:\ANACONDA\python.exe tools/smoke/smoke_wecom_clip_collect.py
        --room-id after_sales --mode pageup --hold --pages 40 --stable 3 --yes

产物：tools/_scratch/wecom_clip/out/<YYYYmmdd_HHMMSS>/（batch_XX.txt + meta.json
+ messages.json）。入库：core.wecom_clip.import_batches_dir(该目录, room_id=...)。
"""
import argparse
import ctypes
import json
import os
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core import wecom_clip as parser          # noqa: E402
from win_api import wecom_clip_driver as drv   # noqa: E402

#: 采集产物落 tools/_scratch（AGENTS.md §2.3：一切自动生成物都进这里）
OUT_ROOT = os.path.join(PROJECT_ROOT, "tools", "_scratch", "wecom_clip", "out")


def _selftest() -> int:
    """只读自检：不激活窗口、不发输入事件，用来确认绑定与环境没坏"""
    print("[selftest] 只读自检，不发任何输入事件：")
    print("  sizeof(INPUT)           = {}  (x64 必须 40)".format(
        ctypes.sizeof(drv.INPUT)))
    print("  sizeof(WINDOWPLACEMENT) = {}".format(
        ctypes.sizeof(drv.WINDOWPLACEMENT)))
    print("  DPI 感知                = {}".format(drv.set_dpi_aware()))
    print("  to_absolute(100,100)    = {}".format(drv.to_absolute(100, 100)))
    clip = drv.read_clipboard()
    print("  剪贴板可读               = {} 字符，开头 {!r}".format(
        len(clip), clip[:60]))
    print("  解析器可导入             = {}".format(parser.SOURCE))
    print("  候选窗口                 = {}".format(
        [w["title"][:30] for w in drv.find_windows("wxwork.exe")] or "未找到企微窗口"))
    return 0


def _parse_start(text: str):
    try:
        x, y = (int(v) for v in text.split(","))
        return x, y
    except Exception:
        raise SystemExit("--start 需要形如 1200,900 的形式")


def _write_artifacts(out_dir, run, args, wins) -> list:
    for idx, text in run.batches:
        with open(os.path.join(out_dir, "batch_{:02d}.txt".format(idx)),
                  "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
    with open(os.path.join(out_dir, "meta.json"), "w", encoding="utf-8") as fh:
        json.dump({
            "room_id": args.room_id, "collected_at": os.path.basename(out_dir),
            "window": {"hwnd": run.hwnd, "title": wins[args.window]["title"],
                       "rect": list(run.rect)},
            "drag": {"x": run.start[0], "y_from": run.start[1],
                     "y_to": run.start[1] - run.drag_px},
            "mode": run.mode, "rounds": run.chars, "aborted": run.aborted,
            "dpi": run.dpi, "screen": list(run.screen),
        }, fh, ensure_ascii=False, indent=2)

    batches = parser.load_batches(out_dir)
    msgs, warns, stats = parser.parse_batches(
        batches, room_id=args.room_id, collected_at=None)
    unique = parser.dedup_messages(msgs)

    # 批次连续性自检：相邻两批**应当有重叠**（同一批消息被两屏各复制一次）。
    # 完全没有重叠说明翻页跨过了整屏，中间有消息被漏掉。
    per_batch = []
    for idx, text in batches:
        one, _w = parser.parse_clip_text(text, room_id=args.room_id,
                                         batch_index=idx)
        per_batch.append((idx, set(m.msg_id for m in one)))
    gaps = ["第 {} 批与第 {} 批没有任何重合消息".format(i1, i2)
            for (i1, s1), (i2, s2) in zip(per_batch, per_batch[1:])
            if s1 and s2 and not (s1 & s2)]

    with open(os.path.join(out_dir, "messages.json"), "w", encoding="utf-8") as fh:
        json.dump({
            "room_id": args.room_id,
            "batches": stats["batches"], "raw_messages": len(msgs),
            "unique_messages": len(unique),
            "warnings": [w.__dict__ for w in warns],
            "messages": [m.__dict__ for m in unique],
        }, fh, ensure_ascii=False, indent=2)
    return [unique, msgs, warns, gaps, stats]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="企微聊天记录采集冒烟（拖选 + PageUp/滚轮 + Ctrl+C）")
    ap.add_argument("--proc", default=drv.TARGET_PROC, help="目标进程名，默认 wxwork.exe")
    ap.add_argument("--window", type=int, default=0, help="候选窗口序号，默认 0（最大的那个）")
    ap.add_argument("--start", help="拖拽起点 x,y（不给就蜂鸣后读鼠标位置）")
    ap.add_argument("--drag-px", type=int, default=0, help="向上拖拽的像素数，默认窗口高 55%%")
    ap.add_argument("--pages", type=int, default=40, help="最多翻多少轮")
    ap.add_argument("--stable", type=int, default=3, help="连续几轮剪贴板不变就停（默认 3）")
    ap.add_argument("--pre-delay", type=float, default=0.0,
                    help="激活企微之前留给用户切群的秒数（期间控制台可见）")
    ap.add_argument("--hold", action="store_true",
                    help="翻页期间一直按住左键（对应手工验证的姿势）")
    ap.add_argument("--mode", choices=("pageup", "scroll"), default="pageup",
                    help="pageup=按住左键+PageUp+微移鼠标（默认，真机已验证）；"
                         "scroll=滚轮翻页后重新拖选")
    ap.add_argument("--notches", type=int, default=4,
                    help="scroll 模式每轮向上滚的格数（一格 120）")
    ap.add_argument("--jiggle", type=int, default=14,
                    help="pageup+hold 模式每轮在选区上沿额外移动的像素（触发选区更新）")
    ap.add_argument("--hover-delay", type=float, default=6.0, help="蜂鸣后留给用户移动鼠标的秒数")
    ap.add_argument("--page-wait", type=float, default=0.7, help="每轮翻页之后的等待")
    ap.add_argument("--copy-wait", type=float, default=0.45, help="Ctrl+C 之后的等待")
    ap.add_argument("--room-id", default="", help="来源群标识，写进消息契约")
    ap.add_argument("--no-maximize", action="store_true", help="不最大化窗口")
    ap.add_argument("--selftest", action="store_true", help="只读自检：结构体/坐标/剪贴板/解析器")
    ap.add_argument("--probe", action="store_true", help="只读侦察：列窗口，不发输入")
    ap.add_argument("--dry-run", action="store_true", help="只算几何，不发输入")
    ap.add_argument("--yes", action="store_true", help="跳过回车确认")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()

    dpi = drv.set_dpi_aware()
    print("DPI 感知模式：{}".format(dpi))
    print("屏幕：{}x{}".format(*drv.screen_size()))

    wins = drv.find_windows(args.proc)
    if not wins:
        print("没找到进程名为 {} 的可见窗口。请确认企业微信已登录并打开。".format(args.proc))
        return 3
    print("\n找到 {} 个候选窗口：".format(len(wins)))
    for i, w in enumerate(wins):
        print("  [{}] hwnd={} {}x{} @({},{}) {} 标题={!r}".format(
            i, w["hwnd"], w["width"], w["height"], w["rect"][0], w["rect"][1],
            "前台" if w["foreground"] else ("最小化" if w["minimized"] else "后台"),
            w["title"][:60]))

    if args.probe:
        print("\n当前鼠标位置：{}  ← --probe 只读，没有发任何输入事件".format(
            drv.cursor_pos()))
        return 0

    if args.window >= len(wins):
        print("--window {} 超范围（只有 {} 个候选窗口）".format(args.window, len(wins)))
        return 2
    hwnd = wins[args.window]["hwnd"]
    print("\n使用窗口 [{}] hwnd={}".format(args.window, hwnd))

    left, top, right, bottom = drv.window_rect(hwnd)
    start = _parse_start(args.start) if args.start else None
    drag_px = args.drag_px or drv.default_drag_px(hwnd)
    print("窗口矩形：({}, {})-({}, {})".format(left, top, right, bottom))
    print("计划：拖拽长度 {}px；翻页上限 {} 轮；连续 {} 轮剪贴板不变即停".format(
        drag_px, args.pages, args.stable))

    if args.dry_run:
        anchor = start or (left + (right - left) // 2, bottom - 120)
        print("\n[dry-run] 拖拽起点 = {}，终点 = ({}, {})".format(
            anchor, anchor[0], anchor[1] - drag_px))
        print("[dry-run] 没有发送任何输入事件。")
        return 0

    print("\n" + "=" * 72)
    print("即将独占桌面。步骤：")
    print("  1) 激活并最大化企业微信")
    print("  2) 蜂鸣 3 声后，有 {} 秒把鼠标移到【目标群消息列表的底部中央】".format(
        args.hover_delay))
    print("  3) 之后不要碰鼠标键盘，直到脚本自己结束")
    print("急停：任何时候按 Esc")
    print("=" * 72)
    if not args.yes:
        try:
            input("按回车继续，Ctrl+C 放弃 > ")
        except KeyboardInterrupt:
            print("\n已放弃。")
            return 1

    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(OUT_ROOT, stamp)
    os.makedirs(out_dir, exist_ok=True)

    run = drv.collect_stream(
        hwnd, drag_px=args.drag_px, pages=args.pages, mode=args.mode,
        notches=args.notches, jiggle=args.jiggle, start=start,
        hover_delay=args.hover_delay, page_wait=args.page_wait,
        copy_wait=args.copy_wait, stable=args.stable, hold=args.hold,
        pre_delay=args.pre_delay, maximize=not args.no_maximize, log=print)
    if run.aborted:
        print("结束原因：{}".format(run.aborted))
    print("\n采集完成：{} 个批次，逐轮字符数 {}".format(len(run.batches), run.chars))

    unique, msgs, warns, gaps, stats = _write_artifacts(out_dir, run, args, wins)

    print("\n" + "=" * 72)
    print("产物目录：{}".format(out_dir))
    print("批次 {} 个；原始消息 {} 条；去重后 {} 条".format(
        stats["batches"], len(msgs), len(unique)))
    if unique:
        print("时间范围：{} ~ {}".format(unique[0].msg_ts, unique[-1].msg_ts))
    empties = [m for m in unique if m.msg_type == "empty"]
    if empties:
        print("⚠ 正文为空（图片/文件类，剪贴板拿不到）的消息：{} 条".format(len(empties)))
    if warns:
        codes = {}
        for w in warns:
            codes[w.code] = codes.get(w.code, 0) + 1
        print("告警：{}".format(", ".join(
            "{}={}".format(k, v) for k, v in sorted(codes.items()))))
    if gaps:
        print("⚠ 批次不连续（可能有漏采）：")
        for g in gaps:
            print("   " + g)
    else:
        print("批次连续性：相邻批次均有重叠，没有明显漏采。")
    print("入库：core.wecom_clip.import_batches_dir({!r}, room_id={!r})".format(
        out_dir, args.room_id))
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
