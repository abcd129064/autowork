# -*- coding: utf-8 -*-
"""大华设备网络 SDK 封装——进程内单例

职责边界：
- 定位并加载 vendor/dahua/NetSDK（开发态 / PyInstaller frozen 双路径）；
- SDK 全局生命周期（InitEx / Cleanup 全进程只做一次，dhnetsdk 不支持多实例）；
- 单设备会话：登录 / 预览(渲染到 HWND) / 抓图 / 云台 / OSD 读写；
- 本层**不 import Qt**：回调经调用方注入的纯 Python callable 转交，
  Qt 信号桥由 UI 层（windows/camera/camera_page.py）负责。

签名依据：官方 wheel NetSDK-2.0.0.1（vendor/dahua/NetSDK），已实测坑：
- LoginWithHighLevelSecurity 的 in/out 结构体必须显式填 dwSize，否则 0ms 秒败；
- RealPlayEx(hwnd=原生窗口句柄) 时 SDK 内部自行解码渲染，无需自取码流；
- 数据回调（无窗口模式）必须挂在 realplay 句柄上且 dwFlag=RAW_DATA。

并发约定：bootstrap 有模块锁，保证全进程只初始化一次；DahuaClient 本身不是
线程安全的，同一会话的抓图与云台调用必须串行，否则回调里的 login_id 会串台。
"""
from __future__ import annotations

import logging
import os
import sys
import threading
from typing import Callable, Optional

logger = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 三个模块级状态：已初始化标志、初始化互斥锁、模块命名空间缓存。都放在模块上
# 而不是实例上，因为 dhnetsdk 全进程只允许一份初始化。
_NETSDK_BOOTED = False
_NETSDK_LOCK = threading.Lock()
_NETSDK_MOD = None  # import 后的 NetSDK 模块命名空间缓存


class DahuaSdkError(Exception):
    """本层对外的唯一异常类型。

    SDK 返回的整数错误码在这里翻成中文消息后再抛：调用方只需展示，不必
    再理解 vendor 的错误码体系；要原始码时看打出的 GetLastErrorMessage。
    """


# ---------------------------------------------------------------- 加载


def _candidate_dirs() -> list:
    """按优先级列出可能包含 NetSDK/ 子目录的路径。

    顺序即优先级：环境变量是排障用的临时覆盖，frozen 下先看解包目录再看
    exe 同级，最后才回落仓库的 vendor 目录。
    """
    dirs = []
    env = os.environ.get("DAHUA_NETSDK_PATH")
    if env:
        dirs.append(env)
    # frozen 时 ROOT 指向 _MEIPASS 的临时目录，重启后路径就变了，
    # 所以还得额外试一次 exe 同级的 dahua 目录
    if getattr(sys, "frozen", False):  # PyInstaller：资源解包目录
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            dirs.append(os.path.join(meipass, "dahua"))
        dirs.append(os.path.join(os.path.dirname(sys.executable), "dahua"))
    dirs.append(os.path.join(ROOT, "vendor", "dahua"))
    return dirs


def bootstrap() -> None:
    """定位 vendor/dahua/NetSDK 并完成 SDK 全局初始化（幂等，线程安全）"""
    global _NETSDK_BOOTED, _NETSDK_MOD
    with _NETSDK_LOCK:
        if _NETSDK_BOOTED:
            return
        # 命中第一个完整包含 NetSDK.py 的候选就停：多个副本同时存在时，
        # 插进 sys.path 顺序不同会让 import 到不同版本
        for base in _candidate_dirs():
            pkg = os.path.join(base, "NetSDK")
            if not os.path.isfile(os.path.join(pkg, "NetSDK.py")):
                continue
            if base not in sys.path:
                sys.path.insert(0, base)
            # 让系统找到 Libs/win64 下的依赖 DLL（libeay32 等）
            # SDK 自带 libeay32/ssleay32，Python 3.8+ 不再从 PATH 里找依赖
            # DLL，必须显式登记搜索目录
            libs = os.path.join(pkg, "Libs", "win64")
            if os.path.isdir(libs) and hasattr(os, "add_dll_directory"):
                try:
                    os.add_dll_directory(libs)
                except OSError:
                    pass
            from NetSDK import NetSDK as _ns  # noqa: PLC0415

            _NETSDK_MOD = _ns
            sdk = _ns.NetClient()
            sdk.InitEx(None)  # 断线回调由会话层按需接管
            _NETSDK_BOOTED = True
            logger.info("Dahua NetSDK 初始化完成（%s）", base)
            return
        # 所有候选都没命中：这是安装包缺文件而不是设备问题，
        # 错误消息里要直接给出修复路径，否则现场只会看到"登录失败"
        raise DahuaSdkError(
            "未找到大华 NetSDK 包（vendor/dahua/NetSDK）。"
            "可设置环境变量 DAHUA_NETSDK_PATH 指向包含 NetSDK/ 子目录的路径。"
        )


def sdk():
    """返回一个已初始化的 NetClient（必要时先 bootstrap）。

    每次调用都新建实例：SDK 内部按句柄管理会话，复用一个实例会让多路
    预览的句柄互相覆盖。
    """
    bootstrap()
    return _NETSDK_MOD.NetClient()


def module():
    """返回 NetSDK 模块命名空间（枚举、结构体、sizeof 都从这里取）。

    结构体类型必须与 SDK 是同一份定义，因此不额外缓存到本模块变量，统一
    从入口取，避免出现两份互不兼容的 ctypes 类型。
    """
    bootstrap()
    return _NETSDK_MOD


# ---------------------------------------------------------------- 会话


class DahuaClient:
    """单台设备的登录会话；UI 层持有实例，离线或换机时 close()。

    一个实例对应一条 login 句柄，构造即登录，没有单独的 connect 步骤。
    不保证线程安全：预览与抓图回调都直接比对 self._login_id。
    """

    def __init__(self, host: str, port: int, user: str, password: str):
        """登录设备并读取序列号与通道数；失败直接抛 DahuaSdkError。

        构造即登录：会话对象存在就代表已登录，调用方不需要判断中间态，
        也就不存在"登录了但忘了标记"这种半死状态。
        """
        m = module()
        self._m = m
        self._sdk = m.NetClient()
        self._sdk.InitEx(None)
        in_param = m.NET_IN_LOGIN_WITH_HIGHLEVEL_SECURITY()
        in_param.dwSize = m.sizeof(in_param)  # ⚠ 必填，漏了 0ms 秒败
        # 字符串字段按 SDK 约定传 bytes（UTF-8）；emSpecCap=TCP 表示走
        # SDK 自己的登录握手，不是明文传输
        in_param.szIP = host.encode()
        in_param.nPort = int(port)
        in_param.szUserName = user.encode()
        in_param.szPassword = password.encode()
        in_param.emSpecCap = m.EM_LOGIN_SPAC_CAP_TYPE.TCP
        in_param.pCapParam = None
        out_param = m.NET_OUT_LOGIN_WITH_HIGHLEVEL_SECURITY()
        out_param.dwSize = m.sizeof(out_param)
        self._login_id, dev_info, err_msg = self._sdk.LoginWithHighLevelSecurity(
            in_param, out_param)
        if not self._login_id:
            raise DahuaSdkError(f"登录失败：{err_msg}（{host}:{port}）")
        self.host, self.port = host, int(port)
        self.serial = self._decode(dev_info, ("sSerialNumber", "szSerialNumber"))
        # 通道数缺省按 1：部分固件不回 nChanNum，按 1 处理比直接报错实用
        self.chan_num = int(getattr(dev_info, "nChanNum", 1) or 1)
        # 预览句柄单独记：close() 里可能还没预览过，用 0 表示未开流
        self._play_id = 0
        logger.info("大华登录成功 %s:%s serial=%s", host, port, self.serial)

    @staticmethod
    def _decode(info, names):
        """按候选字段名取字符串，全部缺失时返回空串。

        序列号在不同固件里叫 sSerialNumber 或 szSerialNumber，逐个试；
        bytes 尾部用 \x00 补齐，不裁掉会让序列号比较永远不相等。
        """
        for n in names:
            v = getattr(info, n, None)
            if v is None:
                continue
            b = v if isinstance(v, bytes) else bytes(v)
            return b.decode(errors="replace").strip("\x00").strip()
        return ""

    # ---------- 生命周期 ----------

    def close(self) -> None:
        """停预览并登出；重复调用无副作用。

        异常一律吞掉：设备已离线时登出必然失败，此时抛出只会中断调用方的
        关闭流程，留下更脏的状态。
        """
        try:
            if self._play_id:
                self._sdk.StopRealPlayEx(self._play_id)
                self._play_id = 0
            if getattr(self, "_login_id", 0):
                self._sdk.Logout(self._login_id)
        except Exception:  # noqa: BLE001 登出失败不应阻断调用方
            logger.warning("登出异常（忽略）", exc_info=True)
        finally:
            self._login_id = 0

    @property
    def login_id(self) -> int:
        """当前登录句柄；close() 之后为 0。"""
        return self._login_id

    # ---------- 预览 ----------

    def realplay_start(self, hwnd: int, channel: int = 0, sub_stream: bool = True) -> int:
        """把画面渲染到原生窗口句柄（SDK 内部解码，无需自取码流）"""
        # 已有一路预览就复用：同会话开第二路会抢同一个 HWND，
        # 表现为画面闪一下随即黑屏
        if self._play_id:
            return self._play_id
        # 默认子码流：UI 只做监看，子码流分辨率低、解码压力小；要清晰画面
        # 时由调用方显式传 sub_stream=False
        ptype = (self._m.SDK_RealPlayType.Realplay_1 if sub_stream
                 else self._m.SDK_RealPlayType.Realplay)
        play_id = self._sdk.RealPlayEx(self._login_id, channel, int(hwnd), ptype)
        if not play_id:
            raise DahuaSdkError(f"启动预览失败：{self._sdk.GetLastErrorMessage()}")
        self._play_id = play_id
        return play_id

    def realplay_stop(self) -> None:
        """停止预览并丢弃句柄；没在预览时是空操作。"""
        if self._play_id:
            self._sdk.StopRealPlayEx(self._play_id)
            self._play_id = 0

    # ---------- 抓图 ----------

    def snap_to_file(self, path: str, channel: int = 0) -> str:
        """抓一张 JPEG 落盘，返回路径。

        走经典路径 SnapPictureEx + fSnapRev 回调（官方 ImageTest.exe 同款）；
        SnapPictureToFile 在部分 IPC 上报 "User parameter is illegal"（10-07 实测）。
        """
        import time as _time
        m = self._m
        # 回调跑在 SDK 自己的线程里，用 Event + dict 把结果交回当前线程；
        # 在回调里直接写文件会与主线程抢 GIL，也无法处理超时
        done = threading.Event()
        holder: dict = {}

        def on_snap(l_login_id, p_buf, rev_len, _encode_type, _cmd_serial, _user):
            # CmdSerial 由 SDK 内部生成，按 login_id 匹配即可（单会话串行抓图）
            if l_login_id != self._login_id or not rev_len:
                return
            import ctypes as _ct
            holder["data"] = _ct.string_at(p_buf, rev_len)
            done.set()

        cb = m.fSnapRev(on_snap)
        self._sdk.SetSnapRevCallBack(cb, 0)
        self._snap_cb = cb  # 防 GC
        # Quality=5、ImageSize=0 即原图 JPEG；InterSnap=0 表示不按间隔连拍
        par = m.SNAP_PARAMS(Channel=int(channel), Quality=5, ImageSize=0,
                            mode=0, InterSnap=0)
        if not self._sdk.SnapPictureEx(self._login_id, par):
            raise DahuaSdkError(f"抓图请求失败：{self._sdk.GetLastErrorMessage()}")
        # 6 秒上限：走隧道时正常抓图 1~2 秒，超过 6 秒基本是设备没回包
        if not done.wait(6.0):
            raise DahuaSdkError("抓图超时（6s 未收到图像回调）")
        with open(path, "wb") as f:
            f.write(holder["data"])
        return path

    # ---------- 云台 ----------

    def ptz(self, command: str, speed: int = 4, stop: bool = False) -> None:
        """方向/变倍/聚焦/光圈；command 见 PTZ_MAP；方向类按住发 start、松开发 stop"""
        PTZ = self._m.SDK_PTZ_ControlType
        cmd = getattr(PTZ, command)
        # 速度夹在 1~8：SDK 只接受这个区间，而 0 在部分固件上表示"用设备
        # 默认值"，按住方向键会完全不动
        l1, l2, l3 = 0, max(1, min(8, int(speed))), 0
        if command in ("POINT_MOVE_CONTROL", "POINT_SET_CONTROL", "POINT_DEL_CONTROL"):
            l1, l2 = 0, l2  # param2 = 预置点序号（调用方先 ptz_preset 封装，一般不走本方法）
        # stop=True 才是停止指令：方向类按键必须在松开时再发一次，否则镜头
        # 会一直转下去，只能靠断电恢复
        if not self._sdk.PTZControlEx2(self._login_id, 0, cmd, l1, l2, l3, bool(stop)):
            raise DahuaSdkError(f"云台命令 {command} 失败：{self._sdk.GetLastErrorMessage()}")

    def ptz_preset(self, action: str, index: int) -> None:
        """预置点：action ∈ goto/set/del"""
        PTZ = self._m.SDK_PTZ_ControlType
        # action 到控制类型的映射写死在这里：只有三种操作，而传错枚举值时
        # SDK 不会报错，只会静默无效
        cmd = {"goto": PTZ.POINT_MOVE_CONTROL,
               "set": PTZ.POINT_SET_CONTROL,
               "del": PTZ.POINT_DEL_CONTROL}[action]
        if not self._sdk.PTZControlEx2(self._login_id, 0, cmd, 0, int(index), 0, False):
            raise DahuaSdkError(f"预置点 {action}#{index} 失败：{self._sdk.GetLastErrorMessage()}")

    # ---------- 预置点列表 ----------

    _PRESET_MAX = 300

    def ptz_presets(self) -> list:
        """读取相机内预置点列表，返回 [{'index': int, 'name': str}] 按 index 升序。

        走 QueryDevState(PTZ_PRESET_LIST=0x57)（老通道，该固件支持；
        新版 CLIENT_PTZGetPreset 报 "device not found com interface"）。
        设备名为空或默认名时回退本地别名（UI 命名存 app_settings，
        因该固件无名称写入接口）。
        """
        m = self._m
        # 结构体数组在 Python 侧分配，再把指针塞进 lst：SDK 只往里填内容，
        # 不负责分配内存，所以数组生命周期必须覆盖整个调用
        arr = (m.NET_PTZ_PRESET * self._PRESET_MAX)()
        lst = m.NET_PTZ_PRESET_LIST()
        lst.dwSize = m.sizeof(lst)
        lst.dwMaxPresetNum = self._PRESET_MAX
        lst.pstuPtzPorsetList = m.cast(arr, m.POINTER(m.NET_PTZ_PRESET))
        try:
            st = int(m.EM_QUERY_DEV_STATE_TYPE.PTZ_PRESET_LIST)  # 0x0057
        except AttributeError:
            st = 0x57
        if not self._sdk.QueryDevState(self._login_id, st, lst,
                                       m.sizeof(lst), 0, 2000):
            raise DahuaSdkError(
                f"读取预置点列表失败：{self._sdk.GetLastErrorMessage()}")
        aliases = self.preset_aliases()
        out = []
        for i in range(int(lst.dwRetPresetNum)):
            it = arr[i]
            # 设备是否写过自定义名看 bSetNameEx；没写过时 szNameEx 是空的
            raw = it.szNameEx if it.bSetNameEx else it.szName
            name = bytes(raw).split(b"\x00")[0].decode("gbk",
                                                       errors="replace").strip()
            idx = int(it.nIndex)
            # 设备默认名（"预置点1"）按未命名处理，改用本地别名；这批固件
            # 没有名称写入接口，别名只能存本地
            if not name or name == f"预置点{idx}":
                name = aliases.get(str(idx)) or name or f"预置点{idx}"
            out.append({"index": idx, "name": name})
        out.sort(key=lambda p: p["index"])
        return out

    def preset_aliases(self) -> dict:
        """本地预置点别名 {str(index): name}（按设备序列隔离，存 app_settings）"""
        from core import app_settings
        return (app_settings.get("dahua_preset_alias", {}) or {}).get(
            self.serial, {})

    def set_preset_alias(self, index: int, name: str) -> None:
        """写入本地预置点别名，按设备序列号隔离。

        存 app_settings 而不是设备：固件没有名称写入接口，真机改名会被拒，
        本地别名是唯一可行方案。
        """
        from core import app_settings
        all_map = app_settings.get("dahua_preset_alias", {}) or {}
        dev = dict(all_map.get(self.serial, {}))
        dev[str(int(index))] = name
        all_map[self.serial] = dev
        app_settings.set("dahua_preset_alias", all_map)

    def ptz_supported(self, channel: int = 0) -> bool:
        """云台能力探测。

        ⚠ 语义是 fail-open：查询失败/缓冲不够一律按「支持云台」处理。
        教训（2026-10-07 真机）：4238 是云台机，但 PTZ_LOCATION 查询因
        缓冲不足报 "There is no sufficient buffer"，旧逻辑把「查询失败」
        当「不支持」→ 整个云台面板被误灰。官方 Demo 也不做能力门控，
        固定镜头上 PTZ 指令只是无效果，代价远小于误灰整个面板。
        只有查询成功且返回内容全零/为空才判「固定镜头」。
        """
        m = self._m
        try:
            state_type = int(m.EM_QUERY_DEV_STATE_TYPE.PTZ_LOCATION)  # 0x0036
        except AttributeError:
            state_type = 0x36
        ok = False
        for size in (512, 4096, 16384):  # 官方固件对缓冲长度敏感，逐级放大
            buf = m.create_string_buffer(size)
            ok = bool(self._sdk.QueryDevState(self._login_id, state_type, buf,
                                              size, 0, 1000))
            if ok:
                return any(buf)  # 成功：内容全零/空 = 无云台
        logger.info("PTZ_LOCATION 查询失败（按支持云台处理）：%s",
                    self._sdk.GetLastErrorMessage())
        return True

    # ---------- OSD ----------

    _BLEND_MAIN = 1      # EM_A_NET_EM_OSD_BLEND_TYPE.MAIN
    _BLEND_PREVIEW = 6   # …PREVIEW

    def _osd_cfg(self, cfg_id: int, channel: int, struct_obj=None, read: bool = False):
        """读写一条 OSD 配置，按 read 选择 GetConfig / SetConfig。

        两个分支的入参形态不同（读要缓冲区、写要结构体实例），这是官方包装
        的硬要求，写反了会抛 TypeError 而不是返回错误码。
        """
        m = self._m

        def _call():
            if read:
                # ⚠ GetConfig 的 out 缓冲也要求预填 dwSize（SDK 校验入参完整性），
                #   零填充缓冲会报 "dwSize is not initialized"
                size = m.sizeof(struct_obj)
                buf = m.create_string_buffer(size)
                m.memmove(buf, m.byref(struct_obj), size)
                if not self._sdk.GetConfig(self._login_id, cfg_id, channel, buf,
                                           size, 2000, None):
                    raise DahuaSdkError(
                        f"读取 OSD 配置({cfg_id})失败：{self._sdk.GetLastErrorMessage()}")
                return struct_obj.__class__.from_buffer_copy(buf)
            # ⚠ 官方包装 SetConfig 内部已做 byref(szInBuffer)，这里必须传结构体实例，
            #   再 byref 会炸 TypeError（byref argument must be a ctypes instance）
            if not self._sdk.SetConfig(self._login_id, cfg_id, channel,
                                       struct_obj, m.sizeof(struct_obj),
                                       2000, 0, None):
                raise DahuaSdkError(
                    f"写入 OSD 配置({cfg_id})失败：{self._sdk.GetLastErrorMessage()}")

        return self._with_retry(_call)

    def _with_retry(self, fn, retries: int = 1, pause: float = 0.4):
        """隧道下配置请求背靠背会 "Protocol error/timeout"（2026-10-07 实测：
        无间距 24 请求挂 4，300ms 间距 0 挂）。执行层另有节流，这里兜底重试。"""
        import time as _t
        last: Exception | None = None
        for _ in range(retries + 1):
            try:
                return fn()
            except DahuaSdkError as e:
                msg = str(e)
                last = e
                if "Protocol error" not in msg and "timeout" not in msg.lower():
                    raise
                _t.sleep(pause)
        raise last

    def _read_osd(self, cfg_id: int, channel: int, cls):
        """读 OSD 配置的标准模板：dwSize + emOsdBlendType=MAIN 均为必填读取键，
        缺 dwSize 报 "dwSize is not initialized"、缺 blendType 报 "User parameter is illegal" """
        m = self._m
        tpl = cls()
        tpl.dwSize = m.sizeof(tpl)
        tpl.emOsdBlendType = self._BLEND_MAIN
        return self._osd_cfg(cfg_id, channel, tpl, read=True)

    @staticmethod
    def _apply_front_rgb(st, rgb: tuple) -> None:
        """前景色白字等：nRed/nGreen/nBlue + 不透明 alpha"""
        r, g, b = (int(v) & 0xFF for v in rgb)
        st.stuFrontColor.nRed, st.stuFrontColor.nGreen, st.stuFrontColor.nBlue = r, g, b
        st.stuFrontColor.nAlpha = 255

    def _fill_blend(self, st, blend_on: Optional[bool], rect_xy=None) -> None:
        """按官方 Demo 语义：每码流一次 SetConfig；主码流+预览两条都按 blend_on 落。
        blend_on/rect_xy 为 None 时保留结构体现值（RMW 语义，勿覆盖设备配置）"""
        st.dwSize = self._m.sizeof(st)
        for blend in (self._BLEND_MAIN, self._BLEND_PREVIEW):
            st.emOsdBlendType = blend
            if blend_on is not None:
                st.bEncodeBlend = bool(blend_on)
            if rect_xy is not None:
                st.stuRect.nLeft = st.stuRect.nTop = 0
                st.stuRect.nRight = st.stuRect.nBottom = 0
                st.stuRect.nLeft, st.stuRect.nTop = rect_xy
                st.stuRect.nRight, st.stuRect.nBottom = rect_xy
            yield st

    def osd_channel_title(self, channel: int = 0, text: Optional[str] = None,
                          show: Optional[bool] = None,
                          pos: Optional[tuple] = None,
                          front_rgb: Optional[tuple] = None) -> dict:
        """通道标题：开关/位置（CFG_CHANNELTITLE=1000）+ 名称文字（NewDevConfig "ChannelTitle"）

        ⚠ 开关/位置走**读-改-写**：先取设备现值再只改暴露字段。
        若用零初始化结构体直接 SetConfig，前景/背景色、对齐等未暴露字段会被
        清零（文字变黑）——2026-10-07 真机事故。
        front_rgb=(r,g,b) 可显式指定前景色（默认保留设备现值）。
        """
        m = self._m
        cfg_id = int(m.NET_EM_CFG_OPERATE_TYPE.CFG_CHANNELTITLE)  # 1000
        if show is not None or pos is not None or front_rgb is not None:
            st = self._read_osd(cfg_id, channel, m.NET_OSD_CHANNEL_TITLE)
            if front_rgb is not None:
                self._apply_front_rgb(st, front_rgb)
            for st_i in self._fill_blend(st, show, pos):
                self._osd_cfg(cfg_id, channel, st_i)
        name_applied: Optional[bool] = None
        if text is not None:
            try:
                self._set_channel_name(channel, text)
                name_applied = True
            except DahuaSdkError as e:
                # 4238 固件实测：SetNewDevConfig 6 种载荷形态（table/JSON×
                # id+params/params/table × utf-8/ascii 转义）+ SetNewDevConfigForWeb
                # 全部拒绝写通道名称 → 降级：保留设备原名称，其余 OSD 照常应用
                logger.warning("通道名称写入被固件拒绝（保留原名称）: %s", e)
                name_applied = False
        result = self.osd_channel_title_read(channel)
        if name_applied is not None:
            result["name_applied"] = name_applied
        return result

    def osd_channel_title_read(self, channel: int = 0) -> dict:
        """读回通道标题的开关、位置与名称，作为写操作的统一返回值。"""
        m = self._m
        st = m.NET_OSD_CHANNEL_TITLE()
        st.dwSize = m.sizeof(st)
        st.emOsdBlendType = self._BLEND_MAIN
        got = self._osd_cfg(1000, channel, st, read=True)
        return {"show": bool(got.bEncodeBlend),
                "pos": (int(got.stuRect.nLeft), int(got.stuRect.nTop)),
                "name": self._get_channel_name(channel)}

    def osd_time_title(self, channel: int = 0, show: Optional[bool] = None,
                       show_week: Optional[bool] = None,
                       pos: Optional[tuple] = None,
                       front_rgb: Optional[tuple] = None) -> dict:
        """时间标题（CFG_TIMETITLE=1001），读-改-写语义同 osd_channel_title"""
        m = self._m
        st = self._read_osd(1001, channel, m.NET_OSD_TIME_TITLE)  # 纯读/写前取基线共用
        if show is not None or pos is not None or show_week is not None \
                or front_rgb is not None:
            if front_rgb is not None:
                self._apply_front_rgb(st, front_rgb)
            for st_i in self._fill_blend(st, show, pos):
                if show_week is not None:  # 未指定时保留设备现值，勿覆盖
                    st_i.bShowWeek = bool(show_week)
                self._osd_cfg(1001, channel, st_i)
            # 写完重读：设备会把位置取整、日期格式归一化，回显以设备为准
            st = self._read_osd(1001, channel, m.NET_OSD_TIME_TITLE)  # 写后取新值
        return {"show": bool(st.bEncodeBlend), "show_week": bool(st.bShowWeek),
                "pos": (int(st.stuRect.nLeft), int(st.stuRect.nTop))}

    def osd_custom_text(self, channel: int, text: str, show: bool,
                        pos: tuple = (100, 100)) -> None:
        """自定义文字告示（CFG_CUSTOMTITLE=1002，NET_OSD_CUSTOM_TITLE，8 条槽位取第 1 条）"""
        m = self._m
        st = m.NET_OSD_CUSTOM_TITLE()
        st.dwSize = m.sizeof(st)
        st.emOsdBlendType = self._BLEND_MAIN
        # 只用第 1 条槽位：8 条是给设备端滚动告示用的，本工具只需要一条
        st.nCustomTitleNum = 1
        item = st.stuCustomTitle[0]
        # 设备只认 GBK，字段定长 1024，超长直接截断而不是报错
        item.szText = text.encode("gbk", errors="replace")[:1024]
        item.bEncodeBlend = bool(show)
        item.stuRect.nLeft, item.stuRect.nTop = pos
        # 矩形取同一个点：该字段实际是左上角坐标，宽高由设备按字号推算
        item.stuRect.nRight, item.stuRect.nBottom = pos
        self._osd_cfg(1002, channel, st)

    # ---------- 编码配置（码流/分辨率/帧率/码率，ConfigTool「编码配置」同源） ----------

    STREAM_MAIN = 1    # EM_A_NET_EM_FORMAT_TYPE.EM_FORMAT_MAIN_NORMAL
    STREAM_EXTRA1 = 4  # EM_FORMAT_EXTRA1

    def _encode_video_struct(self, stream: int):
        """GetConfig(ENCODE_VIDEO=1100) 读指定码流的完整结构体（RMW 基线）"""
        m = self._m
        st = m.NET_ENCODE_VIDEO_INFO()
        st.dwSize = m.sizeof(st)
        st.emFormatType = int(stream)
        # 与 _read_osd 同款：先把填好 dwSize/emFormatType 的结构体拷进缓冲，
        # 因为 GetConfig 会校验这两个字段是否已初始化
        buf = m.create_string_buffer(m.sizeof(st))
        m.memmove(buf, m.byref(st), m.sizeof(st))
        if not self._sdk.GetConfig(self._login_id, 1100, 0, buf,
                                   m.sizeof(st), 2000, None):
            raise DahuaSdkError(f"读取编码配置失败：{self._sdk.GetLastErrorMessage()}")
        return m.NET_ENCODE_VIDEO_INFO.from_buffer_copy(buf)

    def encode_video(self, stream: int = STREAM_MAIN) -> dict:
        """读码流视频参数。stream: 1=主码流, 4=辅码流1"""
        # 读取也重试：隧道下背靠背 GET 同样会偶发 Protocol error
        g = self._with_retry(lambda: self._encode_video_struct(stream))
        return {"enable": bool(g.bVideoEnable),
                "compression": int(g.emCompression),
                "width": int(g.nWidth), "height": int(g.nHeight),
                "bitrate_control": int(g.emBitRateControl),
                "bitrate": int(g.nBitRate),
                "framerate": float(g.nFrameRate),
                "iframe_interval": int(g.nIFrameInterval),
                "image_quality": int(g.emImageQuality)}

    def set_encode_video(self, stream: int = STREAM_MAIN, *, enable=None,
                         compression=None, width=None, height=None,
                         bitrate=None, bitrate_control=None, framerate=None,
                         iframe_interval=None, image_quality=None) -> dict:
        """RMW 写码流参数：只改给定字段，其余保留设备现值；写后返回新读。
        compression: 7=H.264 8=H.265；bitrate_control: 0=CBR 1=VBR；
        bitrate 单位 kbps；image_quality: 1-6（10%~100%，VBR 下生效）。"""
        m = self._m

        # 把"读-改-写"整体放进重试闭包：只重试写不重读，会拿着过期基线
        # 覆盖掉别人刚改的字段
        def _apply():
            g = self._encode_video_struct(stream)
            if enable is not None:
                g.bVideoEnable = bool(enable)
            if compression is not None:
                g.emCompression = int(compression)
            if width is not None:
                g.nWidth = int(width)
            if height is not None:
                g.nHeight = int(height)
            if bitrate is not None:
                g.nBitRate = int(bitrate)
            if bitrate_control is not None:
                g.emBitRateControl = int(bitrate_control)
            if framerate is not None:
                g.nFrameRate = float(framerate)
            if iframe_interval is not None:
                g.nIFrameInterval = int(iframe_interval)
            if image_quality is not None:
                g.emImageQuality = int(image_quality)
            if not self._sdk.SetConfig(self._login_id, 1100, 0, g,
                                       m.sizeof(g), 2000, 0, None):
                raise DahuaSdkError(
                    f"写入编码配置失败：{self._sdk.GetLastErrorMessage()}")

        # 写成功后重新读一次：设备可能拒绝某些组合（如 4K 配高帧率）并静默
        # 改回，回显设备真实值比回显入参更可信
        self._with_retry(_apply)
        return self.encode_video(stream)

    # ---------- 通道名称文字 ----------
    # 首选 GetConfig/SetConfig(ENCODE_CHANNELTITLE=1108, NET_ENCODE_CHANNELTITLE_INFO
    # 的 szChannelName)——4238 固件实测双向可用（ConfigTool 同款路径）；
    # NewDevConfig("ChannelTitle") 仅作老固件兜底（新固件 GET 回 JSON 但 SET 被拒）。

    _CFG_ENCODE_CHANNELTITLE = 1108

    def _get_channel_name(self, channel: int) -> str:
        """读通道名称：优先编码配置(1108)，失败回落 NewDevConfig 文本。"""
        m = self._m
        st = m.NET_ENCODE_CHANNELTITLE_INFO()
        st.dwSize = m.sizeof(st)
        buf = m.create_string_buffer(m.sizeof(st))
        m.memmove(buf, m.byref(st), m.sizeof(st))
        if self._sdk.GetConfig(self._login_id, self._CFG_ENCODE_CHANNELTITLE,
                               channel, buf, m.sizeof(st), 2000, None):
            got = m.NET_ENCODE_CHANNELTITLE_INFO.from_buffer_copy(buf)
            return bytes(got.szChannelName).split(b"\x00")[0].decode(
                "gbk", errors="replace").strip()
        # 老固件没有 1108 配置项，只能从 NewDevConfig 的文本回包里抠字段
        return self._extract_channel_name_field(self._get_channel_name_raw(channel))

    def _set_channel_name(self, channel: int, text: str) -> None:
        """写通道名称：优先 1108 配置项，被拒时回落 NewDevConfig。

        两条路径都失败才抛异常；上层 osd_channel_title 会接住并把
        name_applied 置 False，其余 OSD 设置照常生效。
        """
        m = self._m
        st = m.NET_ENCODE_CHANNELTITLE_INFO()
        st.dwSize = m.sizeof(st)
        st.szChannelName = text.encode("gbk", errors="replace")[:255]
        if self._sdk.SetConfig(self._login_id, self._CFG_ENCODE_CHANNELTITLE,
                               channel, st, m.sizeof(st), 2000, 0, None):
            return
        # 老固件兜底：NewDevConfig（按 GET 回包格式选 JSON / table）
        raw = self._get_channel_name_raw(channel)
        # 回包是 JSON 说明是新固件，table 文本则是老固件，两种载荷不能混用
        if raw.lstrip().startswith("{"):
            import json
            payload = json.dumps({"table": {"Name": text}},
                                 ensure_ascii=False)
            enc = "utf-8"
        else:
            payload = self._build_channel_name_payload(
                self._extract_channel_name_field(raw), text)
            enc = "gbk"
        err = m.c_int(0)
        # ⚠ 官方包装 SetNewDevConfig 内部对 szInBuffer 做 pointer()——必须传 ctypes
        #   缓冲，传 Python bytes 会炸 TypeError("_type_ must have storage info")
        data = m.create_string_buffer(payload.encode(enc))
        ok = self._sdk.SetNewDevConfig(self._login_id, "ChannelTitle", channel,
                                       data, len(payload), err, 0, 2000)
        if not ok:
            raise DahuaSdkError(f"设置通道名称失败：{self._sdk.GetLastErrorMessage()}")

    def _get_channel_name_raw(self, channel: int) -> str:
        """取 NewDevConfig("ChannelTitle") 的原始文本；失败返回空串。"""
        m = self._m
        buf = m.create_string_buffer(4096)
        err = m.c_int(0)
        ok = self._sdk.GetNewDevConfig(self._login_id, "ChannelTitle", channel,
                                       buf, 4096, err, 2000, None)
        if not ok:
            return ""
        return buf.value.decode(errors="replace")

    @staticmethod
    def _extract_channel_name_field(raw: str) -> str:
        """通道名称提取：新固件 JSON（{"params":{"table":{"Name":..}}}），
        老固件 table 格式（ChannelName[0].Name=..）。"""
        s = raw.strip()
        if s.startswith("{"):
            try:
                import json
                obj = json.loads(s)
                return str(obj.get("params", {}).get("table", {}).get("Name", "") or "")
            except ValueError:
                return ""
        for line in raw.splitlines():
            if line.strip().lower().startswith("channelname[0].name"):
                return line.split("=", 1)[1].strip() if "=" in line else ""
        return ""

    @staticmethod
    def _build_channel_name_payload(existing: str, text: str) -> str:
        """拼老固件的 table 格式载荷。

        已有内容时只发单行赋值：老固件收到整段 SectionCount 载荷会把其他
        字段重置；完全没有既有内容时才发完整段。
        """
        if existing:
            return f"ChannelName[0].Name={text}"
        return (f"ChannelTitle\r\nSectionCount=1\r\n"
                f"ChannelName[0].Name={text}\r\n")


# ---------------------------------------------------------------- 便捷入口


def connect(host_port: str, user: str, password: str) -> DahuaClient:
    """把 '49.235.34.253:4238' 或 '192.168.1.108' 解析成已登录会话。

    纯 IP 时缺省端口 37777；用 rpartition 而不是 split，因为 IPv6 字面量里
    也含冒号，只有最后一个冒号之后才是端口。
    """
    host, _, port = host_port.rpartition(":")
    if not host:  # 无冒号
        host, port = host_port, 37777
    # 顺手剥掉 IPv6 的方括号与空白，调用方不必自己规范化
    return DahuaClient(host.strip("[] "), int(port), user, password)
