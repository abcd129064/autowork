# -*- coding: utf-8 -*-
"""视频画质提升引擎层离线单测（windows/tools/video_enhance.py）

只测纯函数：滤镜链拼装、命令 argv、进度解析、输出命名策略、ffmpeg 定位优先级。
**不碰真 ffmpeg、不起子进程、不联网**——真机链路由 tools/_scratch 里的探针脚本
和 UI 冒烟覆盖，这里要保证的是「在没有 ffmpeg 的机器上 pytest 也全绿」。

顺带锁住两份容易漂移的契约：
  1) 每个画质预设展开后的 -vf 串必须与 design/video_enhance_tool.html 的
     「滤镜预设 → -vf 串」表逐字符一致（那张表自称是 PRESETS 的实际取值）；
  2) 滤镜顺序必须是 去噪 → 放大 → 锐化 → 色彩 → 补帧 → format。顺序错了不会
     报错，只会让画质变差，属于最难被人发现的回归。

运行：E:\\ANACONDA\\python.exe -m pytest tests/test_video_enhance.py -q
"""
import os
import re

import pytest

import windows.tools.video_enhance as ve

# 一份典型的台球录像探测结果，各测试按需覆盖其中几个字段
INFO = {"ok": True, "width": 1280, "height": 720, "fps": 25.0, "duration": 120.0,
        "n_frames": 3000, "size": 100 * 1024 * 1024, "codec_name": "h264",
        "pix_fmt": "yuv420p", "audio_codec": "aac", "bit_rate": 6000000}


def _p(**over):
    """默认参数 + 局部覆盖，省得每个测试都抄一遍 default_params()"""
    p = ve.default_params()
    p.update(over)
    return p


def _idx(chain, *keys):
    """返回各子串在链中的位置，用于断言先后顺序"""
    return {k: chain.find(k) for k in keys}


# ==================== 参数表与预设 ====================

class TestPresets:

    def test_default_params_is_standard_preset(self):
        p = ve.default_params()
        assert p["preset"] == "standard"
        std = ve.PRESETS["standard"]["params"]
        for k, v in std.items():
            assert p[k] == v, f"默认值与 standard 预设不一致: {k}"

    def test_default_params_covers_all_ui_sections(self):
        """UI 五个子页要读的键必须都在，缺一个就是一个静默失效的控件"""
        p = ve.default_params()
        need = ("preset", "denoise_on", "denoise_algo", "denoise_luma",
                "denoise_chroma", "denoise_temporal", "sharpen_on",
                "sharpen_algo", "sharpen_amount", "usm_amount", "usm_lx",
                "usm_ly", "scale_on", "scale_mode", "scale_ratio",
                "scale_height", "scale_algo", "color_on", "contrast",
                "saturation", "brightness", "gamma", "interp_on", "interp_fps",
                "interp_mode", "encoder", "codec_preset", "rate_mode", "crf",
                "target_mb", "audio", "audio_bitrate", "container", "faststart",
                "pix_fmt", "out_mode", "out_dir", "name_tpl", "on_exist")
        missing = [k for k in need if k not in p]
        assert not missing, f"default_params 缺键: {missing}"

    @pytest.mark.parametrize("name", sorted(ve.PRESETS))
    def test_apply_preset_sets_name_and_overrides(self, name):
        p = ve.apply_preset(ve.default_params(), name)
        assert p["preset"] == name
        for k, v in ve.PRESETS[name]["params"].items():
            assert p[k] == v

    def test_apply_preset_unknown_falls_back_to_custom(self):
        p = ve.apply_preset(_p(crf=20), "不存在的预设")
        assert p["preset"] == "custom"
        assert p["crf"] == 20, "未知预设不该动其他参数"

    def test_apply_preset_does_not_mutate_input(self):
        base = ve.default_params()
        snapshot = dict(base)
        ve.apply_preset(base, "anime")
        assert base == snapshot, "apply_preset 改了传入的 dict"

    def test_every_preset_label_and_desc_non_empty(self):
        for name, spec in ve.PRESETS.items():
            assert spec.get("label"), name
            assert spec.get("desc"), name


class TestDesignContract:
    """锁定设计稿「滤镜预设 → -vf 串」表，改预设就必然改到这张表"""

    EXPECTED = {
        "off": "format=yuv420p",
        "light": "hqdn3d=1:1:4:4,cas=0.4,eq=contrast=1.02:saturation=1.03,"
                 "format=yuv420p",
        "standard": "hqdn3d=1.5:1.5:6:6,cas=0.8,eq=contrast=1.04:saturation=1.06,"
                    "format=yuv420p",
        "sharp": "hqdn3d=1:1:4:4,unsharp=5:5:1:5:5:0.0,cas=1,eq=contrast=1.05,"
                 "format=yuv420p",
        "restore": "atadenoise=0a=0.02:0b=0.04:s=80,hqdn3d=2:2:8:8,cas=0.6,"
                   "eq=contrast=1.08:brightness=0.02:saturation=1.08,"
                   "format=yuv420p",
        "anime": "hqdn3d=2:2:6:6,cas=1,eq=contrast=1.06:saturation=1.15,"
                 "format=yuv420p",
    }

    def test_expected_table_covers_every_preset(self):
        assert set(self.EXPECTED) == set(ve.PRESETS)

    @pytest.mark.parametrize("name", sorted(EXPECTED))
    def test_preset_chain_matches_design_table(self, name):
        chain = ve.build_filter_chain(ve.apply_preset(ve.default_params(), name),
                                      INFO)
        assert chain == self.EXPECTED[name]


# ==================== 滤镜链 ====================

class TestFilterChain:

    def test_order_denoise_scale_sharpen_color_interp_format(self):
        chain = ve.build_filter_chain(
            _p(denoise_on=True, denoise_algo="hqdn3d", scale_on=True,
               scale_mode="height", scale_height=1080, sharpen_on=True,
               sharpen_algo="cas", color_on=True, contrast=1.1,
               interp_on=True, interp_fps=50), INFO)
        pos = _idx(chain, "hqdn3d", "scale=", "cas", "eq=", "minterpolate",
                   "format=yuv420p")
        assert all(v >= 0 for v in pos.values()), chain
        assert pos["hqdn3d"] < pos["scale="] < pos["cas"] < pos["eq="] \
            < pos["minterpolate"] < pos["format=yuv420p"], chain

    def test_all_switches_off_leaves_only_format(self):
        chain = ve.build_filter_chain(
            _p(denoise_on=False, sharpen_on=False, color_on=False,
               scale_on=False, interp_on=False), INFO)
        assert chain == "format=yuv420p"

    @pytest.mark.parametrize("switch,frag", [
        ("denoise_on", "hqdn3d"), ("sharpen_on", "cas"), ("color_on", "eq="),
        ("scale_on", "scale="), ("interp_on", "minterpolate")])
    def test_each_switch_removes_its_own_filter(self, switch, frag):
        on = _p(denoise_on=True, sharpen_on=True, color_on=True, scale_on=True,
                scale_height=1080, interp_on=True, contrast=1.1)
        assert frag in ve.build_filter_chain(on, INFO)
        off = dict(on, **{switch: False})
        assert frag not in ve.build_filter_chain(off, INFO)

    def test_pix_fmt_override_is_honoured(self):
        chain = ve.build_filter_chain(_p(denoise_on=False, sharpen_on=False,
                                         color_on=False, pix_fmt="yuv422p"), INFO)
        assert chain == "format=yuv422p"

    def test_format_is_always_last(self):
        for name in ve.PRESETS:
            chain = ve.build_filter_chain(
                ve.apply_preset(_p(interp_on=True, interp_fps=50), name), INFO)
            assert chain.endswith("format=yuv420p"), chain

    def test_bm3d_is_rejected_because_it_needs_filter_complex(self):
        """bm3d 要 split 成两路，-vf 单链会报 Simple filtergraph cannot have split"""
        with pytest.raises(ve.EnhanceError) as e:
            ve.build_filter_chain(_p(denoise_on=True, denoise_algo="bm3d"), INFO)
        assert "filter_complex" in str(e.value)

    def test_bm3d_fragment_really_contains_split(self):
        assert "split[" in ve._denoise_filter(_p(denoise_algo="bm3d"))

    def test_unknown_denoise_algo_raises(self):
        with pytest.raises(ve.EnhanceError, match="未知去噪算法"):
            ve._denoise_filter(_p(denoise_algo="magic"))

    def test_unknown_sharpen_algo_raises(self):
        with pytest.raises(ve.EnhanceError, match="未知锐化算法"):
            ve._sharpen_filter(_p(sharpen_algo="magic"))

    def test_composite_algos_emit_two_filters(self):
        """两级串联是下拉框里的一个选项，但要展开成两个滤镜"""
        assert ve._denoise_filter(_p(denoise_algo="atadenoise+hqdn3d")).count("=") >= 2
        both = ve._sharpen_filter(_p(sharpen_algo="unsharp+cas"))
        assert both.startswith("unsharp=") and both.endswith(",cas=0.8")

    @pytest.mark.parametrize("amount", [1.5, 2.0, 99.0])
    def test_cas_strength_clamped_to_ffmpeg_range(self, amount):
        """cas 的 strength 只收 0..1，越界 ffmpeg 拒绝整条链、整批失败

        旧版滑块量程到 2，用户配置里可能还存着越界值，引擎必须自己兜住。
        """
        assert ve._sharpen_filter(_p(sharpen_algo="cas",
                                     sharpen_amount=amount)) == "cas=1"
        both = ve._sharpen_filter(_p(sharpen_algo="unsharp+cas",
                                     sharpen_amount=amount))
        assert both.endswith(",cas=1")

    def test_every_preset_cas_strength_within_ffmpeg_range(self):
        for name, spec in ve.PRESETS.items():
            amt = spec["params"].get("sharpen_amount", 0.8)
            assert 0 <= amt <= 1, (name, amt)

    def test_nlmeans_rescales_instead_of_copying_hqdn3d_numbers(self):
        """hqdn3d 的 1.5 直接搬给 nlmeans 等于没去噪，量纲差 5 个数量级

        取值必须用带 \\b 的正则："nlmeans=" 里也有一段 "s="，直接 split 会切错。
        """
        f = ve._denoise_filter(_p(denoise_algo="nlmeans", denoise_luma=1.5,
                                  denoise_temporal=6.0))
        assert f.startswith("nlmeans=s=")
        assert float(re.search(r"\bs=([\d.]+)", f).group(1)) < 0.001, f
        assert float(re.search(r"\bp=([\d.]+)", f).group(1)) < 0.001, f

    def test_color_filter_empty_when_all_default(self):
        assert ve._color_filter(_p(contrast=1.0, brightness=0.0, saturation=1.0,
                                   gamma=1.0)) == ""

    def test_color_filter_skips_default_entries(self):
        f = ve._color_filter(_p(contrast=1.05, brightness=0.0, saturation=1.0,
                                gamma=1.0))
        assert f == "eq=contrast=1.05"

    def test_interp_blend_mode_is_cheaper_than_mci(self):
        assert ve._interp_filter(_p(interp_mode="blend", interp_fps=60)) \
            == "minterpolate=fps=60:mi_mode=blend"
        assert "mi_mode=mci" in ve._interp_filter(_p(interp_mode="mci",
                                                     interp_fps=60))

    def test_interp_fps_zero_falls_back_to_60(self):
        """0 在 UI 里是「没填」，按 60 补；负数才是真的关掉补帧"""
        assert "fps=60" in ve._interp_filter(_p(interp_on=True, interp_fps=0))
        assert ve._interp_filter(_p(interp_on=True, interp_fps=-1)) == ""


class TestScaling:

    def test_height_mode_targets_exact_even_height(self):
        assert ve.target_height(_p(scale_on=True, scale_mode="height",
                                   scale_height=1080), INFO) == 1080

    def test_odd_target_is_rounded_up_to_even(self):
        """libx264 直接拒绝奇数高度"""
        assert ve.target_height(_p(scale_on=True, scale_mode="height",
                                   scale_height=1079), INFO) == 1080

    def test_ratio_mode_multiplies_source_height(self):
        assert ve.target_height(_p(scale_on=True, scale_mode="ratio",
                                   scale_ratio=1.5), INFO) == 1080

    def test_scale_off_returns_source_height(self):
        assert ve.target_height(_p(scale_on=False), INFO) == 720

    def test_unknown_source_height_only_breaks_ratio_mode(self):
        """指定高度模式不依赖源高度，探测失败也能出命令；倍数模式没源高度就没法算"""
        assert ve.target_height(_p(scale_on=True, scale_mode="height",
                                   scale_height=1080), {}) == 1080
        assert ve.target_height(_p(scale_on=True, scale_mode="ratio",
                                   scale_ratio=1.5), {}) == 0

    def test_no_scale_and_no_source_height_returns_zero(self):
        """0 的含义是「别插 scale」，_scale_filter 靠它短路"""
        assert ve.target_height(_p(scale_on=False), {}) == 0
        assert ve._scale_filter(_p(scale_on=False), {}) == ""

    def test_scale_filter_uses_minus_two_for_width(self):
        """写死宽度会在奇数宽度的源上把画面拉变形"""
        f = ve._scale_filter(_p(scale_on=True, scale_mode="height",
                                scale_height=1080), INFO)
        assert f == "scale=-2:1080:flags=lanczos"

    def test_scale_algo_is_switchable(self):
        f = ve._scale_filter(_p(scale_on=True, scale_mode="height",
                                scale_height=1080, scale_algo="bicubic"), INFO)
        assert f.endswith("flags=bicubic")

    def test_unknown_scale_algo_raises(self):
        with pytest.raises(ve.EnhanceError, match="未知插值算法"):
            ve._scale_filter(_p(scale_on=True, scale_height=1080,
                                scale_algo="magic"), INFO)

    def test_target_equal_to_source_skips_scale(self):
        """原尺寸 lanczos 重采样不是无损的，只会白花时间还把画面弄软"""
        assert ve._scale_filter(_p(scale_on=True, scale_mode="height",
                                   scale_height=720), INFO) == ""
        chain = ve.build_filter_chain(_p(scale_on=True, scale_mode="height",
                                         scale_height=720), INFO)
        assert "scale=" not in chain

    def test_suffix_placeholder_agrees_with_scale_filter(self):
        """命名里的 _1080p 和链里有没有 scale 必须同进同退"""
        same = _p(scale_on=True, scale_mode="height", scale_height=720,
                  name_tpl="{name}{suffix}")
        stem, _ = ve._fill_template(same["name_tpl"], "a/clip.mp4", INFO, same)
        assert stem == "clip" and ve._scale_filter(same, INFO) == ""
        up = dict(same, scale_height=1080)
        stem, _ = ve._fill_template(up["name_tpl"], "a/clip.mp4", INFO, up)
        assert stem == "clip_1080p" and ve._scale_filter(up, INFO)


# ==================== 命令构建 ====================

class TestBuildCommand:

    def test_progress_flags_always_present(self):
        cmd = ve.build_command("a.mp4", "b.mp4", _p(), INFO)
        assert "-progress" in cmd and "pipe:1" in cmd and "-nostats" in cmd

    def test_crf_mode_has_no_bitrate_and_no_pass(self):
        cmd = ve.build_command("a.mp4", "b.mp4", _p(rate_mode="crf", crf=18), INFO)
        assert "-crf" in cmd and cmd[cmd.index("-crf") + 1] == "18"
        assert "-b:v" not in cmd and "-pass" not in cmd

    def test_size_mode_has_bitrate_and_no_crf(self):
        cmd = ve.build_command("a.mp4", "b.mp4",
                               _p(rate_mode="size", target_mb=100), INFO)
        assert "-b:v" in cmd and "-crf" not in cmd
        assert cmd[cmd.index("-b:v") + 1] == f"{ve.target_bitrate_kbps(100, 120.0)}k"

    def test_size_mode_without_duration_raises(self):
        """算不出码率就必须拦住，否则 ffmpeg 会拿 -b:v 0 编出垃圾"""
        with pytest.raises(ve.EnhanceError, match="目标码率"):
            ve.build_command("a.mp4", "b.mp4", _p(rate_mode="size", target_mb=100),
                             dict(INFO, duration=0.0))

    def test_nvenc_uses_cq_not_crf(self):
        cmd = ve.build_command("a.mp4", "b.mp4",
                               _p(encoder="h264_nvenc", rate_mode="crf", crf=20),
                               INFO)
        assert "-cq" in cmd and "-crf" not in cmd

    def test_codec_preset_dropped_when_not_supported_by_encoder(self):
        """传了编码器不认的档位名，ffmpeg 会当未知参数直接拒绝整条命令"""
        cmd = ve.build_command("a.mp4", "b.mp4",
                               _p(encoder="h264_nvenc", codec_preset="veryslow"),
                               INFO)
        assert "-preset" not in cmd

    def test_codec_preset_kept_when_supported(self):
        cmd = ve.build_command("a.mp4", "b.mp4",
                               _p(encoder="h264_nvenc", codec_preset="p5"), INFO)
        assert cmd[cmd.index("-preset") + 1] == "p5"

    def test_unknown_encoder_raises(self):
        with pytest.raises(ve.EnhanceError, match="未知编码器"):
            ve.build_command("a.mp4", "b.mp4", _p(encoder="libfoo"), INFO)

    def test_faststart_only_for_mp4_family(self):
        assert "-movflags" in ve.build_command("a", "b", _p(container="mp4"), INFO)
        assert "-movflags" not in ve.build_command("a", "b", _p(container="mkv"),
                                                   INFO)

    def test_faststart_switch_off(self):
        assert "-movflags" not in ve.build_command("a", "b",
                                                   _p(faststart=False), INFO)

    def test_output_is_last_argument(self):
        cmd = ve.build_command("a.mp4", "out/x.mp4", _p(), INFO)
        assert cmd[-1] == "out/x.mp4"

    def test_filter_chain_is_passed_verbatim(self):
        p = _p(denoise_on=True, denoise_algo="hqdn3d")
        cmd = ve.build_command("a.mp4", "b.mp4", p, INFO)
        assert cmd[cmd.index("-vf") + 1] == ve.build_filter_chain(p, INFO)


class TestAudioArgs:

    def test_source_without_audio_forces_an(self):
        """源没音轨时 -c:a copy 会报流不存在，整条命令直接失败"""
        assert ve._audio_args(_p(audio="copy"), dict(INFO, audio_codec="")) == ["-an"]

    def test_copy_mode(self):
        assert ve._audio_args(_p(audio="copy"), INFO) == ["-c:a", "copy"]

    def test_none_mode(self):
        assert ve._audio_args(_p(audio="none"), INFO) == ["-an"]

    def test_aac_mode_carries_bitrate(self):
        args = ve._audio_args(_p(audio="aac", audio_bitrate=128), INFO)
        assert args == ["-c:a", "aac", "-b:a", "128k"]

    def test_missing_info_treated_as_no_audio(self):
        assert ve._audio_args(_p(audio="copy"), {}) == ["-an"]


class TestTwoPass:

    def test_libx264_size_mode_yields_two_commands(self):
        cmds = ve.build_two_pass_commands("a.mp4", "b.mp4",
                                          _p(rate_mode="size", target_mb=50),
                                          INFO, "ffmpeg", "log")
        assert len(cmds) == 2

    def test_pass1_discards_output_with_null_muxer(self):
        """pass1 写 os.devnull，mp4 复用器要回写 moov 而 NUL 不可 seek，必须用 null"""
        c1 = ve.build_two_pass_commands("a", "b", _p(rate_mode="size"), INFO,
                                        "ffmpeg", "log")[0]
        assert c1[c1.index("-f") + 1] == "null"
        assert c1[-1] == os.devnull
        assert "-an" in c1
        assert c1[c1.index("-pass") + 1] == "1"

    def test_pass1_keeps_filter_chain(self):
        """分辨率变了第二遍的码率分配才对得上，所以 -vf 不能省"""
        p = _p(rate_mode="size", scale_on=True, scale_mode="height",
               scale_height=1080)
        c1 = ve.build_two_pass_commands("a", "b", p, INFO, "ffmpeg", "log")[0]
        assert "scale=-2:1080" in c1[c1.index("-vf") + 1]

    def test_pass2_writes_real_output(self):
        c2 = ve.build_two_pass_commands("a", "b.mp4", _p(rate_mode="size"), INFO,
                                        "ffmpeg", "log")[1]
        assert c2[-1] == "b.mp4"
        assert c2[c2.index("-pass") + 1] == "2"
        assert "-f" not in c2 or c2[c2.index("-f") + 1] != "null"

    def test_passlogfile_forwarded_to_both_passes(self):
        """并发跑多个任务时共用前缀会互相覆盖统计文件，成品体积完全失控"""
        cmds = ve.build_two_pass_commands("a", "b", _p(rate_mode="size"), INFO,
                                          "ffmpeg", "uniq123")
        for c in cmds:
            assert c[c.index("-passlogfile") + 1] == "uniq123"

    def test_both_passes_use_identical_bitrate(self):
        cmds = ve.build_two_pass_commands("a", "b", _p(rate_mode="size",
                                                        target_mb=50), INFO,
                                          "ffmpeg", "log")
        assert cmds[0][cmds[0].index("-b:v") + 1] == cmds[1][cmds[1].index("-b:v") + 1]

    def test_crf_mode_never_goes_two_pass(self):
        assert len(ve.build_two_pass_commands("a", "b", _p(rate_mode="crf"),
                                              INFO, "ffmpeg", "log")) == 1

    def test_encoder_without_two_pass_support_degrades_to_single(self):
        assert ve.ENCODERS["h264_nvenc"]["two_pass"] is False
        cmds = ve.build_two_pass_commands(
            "a", "b", _p(encoder="h264_nvenc", rate_mode="size"), INFO,
            "ffmpeg", "log")
        assert len(cmds) == 1
        assert "-pass" not in cmds[0]

    def test_cleanup_passlog_removes_both_files(self, tmp_path):
        base = str(tmp_path / "ff2pass")
        for suffix in ("-0.log", "-0.log.mbtree"):
            (tmp_path / f"ff2pass{suffix}").write_text("x")
        ve.cleanup_passlog(base)
        assert not os.path.exists(base + "-0.log")
        assert not os.path.exists(base + "-0.log.mbtree")

    def test_cleanup_passlog_tolerates_empty(self):
        ve.cleanup_passlog("")
        ve.cleanup_passlog(None)


class TestBitrateFormula:

    def test_formula_matches_legacy_script(self):
        """沿用仓库根 video_ai_enhance.py 的算式：1MB=8192kbit，乘 0.98 留给容器头"""
        assert ve.target_bitrate_kbps(100, 100.0) == int(100 * 8192 * 0.98 / 100.0)

    @pytest.mark.parametrize("mb,dur", [(0, 100), (100, 0), (0, 0), (-1, 100),
                                        (100, -5)])
    def test_invalid_inputs_yield_zero(self, mb, dur):
        assert ve.target_bitrate_kbps(mb, dur) == 0


# ==================== 进度解析 ====================

class TestParseProgress:

    def test_out_time_us_is_microseconds(self):
        assert ve.parse_progress("out_time_us=1000000", 4.0) == pytest.approx(25.0)

    def test_out_time_ms_is_also_microseconds(self):
        """这个名字是历史遗留，按毫秒算进度条会快 1000 倍直接冲满"""
        assert ve.parse_progress("out_time_ms=1000000", 4.0) == pytest.approx(25.0)

    def test_out_time_hms_form(self):
        assert ve.parse_progress("out_time=00:00:02.000000", 4.0) \
            == pytest.approx(50.0)

    def test_out_time_hms_with_hours(self):
        assert ve.parse_progress("out_time=01:00:00.000000", 7200.0) \
            == pytest.approx(50.0)

    def test_clamped_to_100(self):
        """补帧会让输出时长超过源时长，不夹住就会显示 150%"""
        assert ve.parse_progress("out_time_us=99000000", 4.0) == 100.0

    def test_never_negative(self):
        assert ve.parse_progress("out_time_us=0", 4.0) == 0.0

    @pytest.mark.parametrize("line", ["frame=123", "speed=1.2x", "",
                                      "progress=continue", "Stream #0:0"])
    def test_non_progress_lines_return_none(self, line):
        assert ve.parse_progress(line, 4.0) is None

    @pytest.mark.parametrize("dur", [0, 0.0, None, -1])
    def test_bad_duration_returns_none(self, dur):
        assert ve.parse_progress("out_time_us=1000000", dur) is None

    def test_speed(self):
        assert ve.parse_speed("speed=2.35x") == pytest.approx(2.35)
        assert ve.parse_speed("speed=   1.4x") == pytest.approx(1.4)

    def test_speed_absent_returns_none(self):
        assert ve.parse_speed("frame=100") is None
        assert ve.parse_speed("") is None

    @pytest.mark.parametrize("line", [
        "out_time=N/A", "out_time_us=N/A", "out_time_ms=N/A",
        "out_time_us=1000000", "frame=12", "fps=25.0", "speed=0.4x",
        "bitrate=N/A", "total_size=N/A", "progress=continue"])
    def test_progress_noise_lines_recognized(self, line):
        """补帧缓冲期 out_time 全是 N/A，parse_progress 返回 None，
        终端过滤必须靠键名兜住，否则 N/A 行刷满输出区"""
        assert ve.is_progress_line(line)

    @pytest.mark.parametrize("line", [
        "", "Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'x.mp4':",
        "Stream #0:0[0x1](und): Video: mpeg4, yuv420p, 960x542",
        "[libx264 @ 000001] kb/s:793.21", "完成: x_enhanced.mp4"])
    def test_readable_lines_are_not_noise(self, line):
        assert not ve.is_progress_line(line)


# ==================== 输出路径 ====================

class TestOutputPath:

    @pytest.fixture
    def src(self, tmp_path):
        f = tmp_path / "clip.mp4"
        f.write_bytes(b"\x00" * 16)
        return str(f)

    def test_default_template(self, src, tmp_path):
        got = ve.resolve_output_path(src, _p(), INFO)
        assert got == str(tmp_path / "clip_enhanced.mp4")

    @pytest.mark.parametrize("tpl,expect", [
        ("{name}_x", "clip_x.mp4"),
        ("{name}_{wxh}", "clip_1280x720.mp4"),
        ("{name}_{preset}", "clip_standard.mp4"),
        ("{name}_{ext}", "clip_mp4.mp4"),
        ("{name}_{date}", None),
        # 没开放大时 {suffix} 是空串，算出来正好撞上源文件，被强制加了序号
        ("{name}{suffix}", "clip_1.mp4"),
    ])
    def test_placeholders(self, src, tmp_path, tpl, expect):
        got = ve.resolve_output_path(src, _p(name_tpl=tpl), INFO)
        if expect is None:
            import time as _t
            expect = f"clip_{_t.strftime('%Y%m%d')}.mp4"
        assert os.path.basename(got) == expect

    def test_wxh_uses_target_height_not_source(self, src):
        got = ve.resolve_output_path(
            src, _p(name_tpl="{name}_{wxh}", scale_on=True, scale_mode="height",
                    scale_height=1080), INFO)
        assert os.path.basename(got) == "clip_1280x1080.mp4"

    def test_suffix_only_when_height_actually_changes(self, src):
        """高度没变就不加 _720p——否则等于宣称做了放大，实际一帧也没重采样"""
        p = _p(scale_on=True, scale_mode="height")
        assert ve._fill_template("{name}{suffix}", src, INFO,
                                 dict(p, scale_height=1080))[0] == "clip_1080p"
        assert ve._fill_template("{name}{suffix}", src, INFO,
                                 dict(p, scale_height=720))[0] == "clip"

    @pytest.mark.parametrize("policy", ["skip", "overwrite", "rename"])
    def test_never_returns_the_source_path_itself(self, src, policy):
        """覆盖策略也不能越过这条底线：dst == src 时 ffmpeg 会一边读一边截断自己
        的输入，源片直接毁掉，而这是用户只填了 {name} 模板就能触发的操作"""
        got = ve.resolve_output_path(src, _p(name_tpl="{name}", on_exist=policy), INFO)
        assert got != src and os.path.basename(got) == "clip_1.mp4"

    def test_empty_template_falls_back_to_default(self, src):
        assert os.path.basename(ve.resolve_output_path(src, _p(name_tpl=""), INFO)) \
            == "clip_enhanced.mp4"

    def test_container_decides_extension(self, src):
        assert ve.resolve_output_path(src, _p(container="mkv"), INFO).endswith(".mkv")

    def test_out_mode_source(self, src, tmp_path):
        assert ve.output_dir(_p(out_mode="source"), src, "Z:/videos") \
            == str(tmp_path)

    def test_out_mode_videos(self, src):
        assert ve.output_dir(_p(out_mode="videos"), src, "Z:/videos") == "Z:/videos"

    def test_out_mode_videos_without_dir_falls_back_to_source(self, src, tmp_path):
        assert ve.output_dir(_p(out_mode="videos"), src, "") == str(tmp_path)

    def test_out_mode_custom(self, src):
        assert ve.output_dir(_p(out_mode="custom", out_dir="D:/out"), src,
                             "Z:/videos") == "D:/out"

    def test_out_mode_custom_without_dir_falls_back(self, src, tmp_path):
        assert ve.output_dir(_p(out_mode="custom", out_dir="  "), src, "") \
            == str(tmp_path)

    def test_unknown_out_mode_falls_back_to_source(self, src, tmp_path):
        assert ve.output_dir(_p(out_mode="???"), src, "Z:/videos") == str(tmp_path)

    def test_on_exist_skip_returns_none(self, src, tmp_path):
        """None 是刻意的第三态：UI 要能区分「跳过」和「失败」"""
        (tmp_path / "clip_enhanced.mp4").write_bytes(b"x")
        assert ve.resolve_output_path(src, _p(on_exist="skip"), INFO) is None

    def test_on_exist_overwrite_returns_same_path(self, src, tmp_path):
        exist = tmp_path / "clip_enhanced.mp4"
        exist.write_bytes(b"x")
        assert ve.resolve_output_path(src, _p(on_exist="overwrite"), INFO) \
            == str(exist)

    def test_on_exist_rename_increments(self, src, tmp_path):
        (tmp_path / "clip_enhanced.mp4").write_bytes(b"x")
        (tmp_path / "clip_enhanced_1.mp4").write_bytes(b"x")
        got = ve.resolve_output_path(src, _p(on_exist="rename"), INFO)
        assert os.path.basename(got) == "clip_enhanced_2.mp4"

    def test_absent_target_returns_path_under_any_policy(self, src):
        for policy in ("skip", "overwrite", "rename"):
            assert ve.resolve_output_path(src, _p(on_exist=policy), INFO)

    def test_resolve_creates_nothing(self, src, tmp_path):
        """算路径不该有副作用，否则「预览输出名」会在磁盘上留空文件"""
        before = set(os.listdir(tmp_path))
        ve.resolve_output_path(src, _p(), INFO)
        assert set(os.listdir(tmp_path)) == before


# ==================== ffmpeg 定位 ====================

class TestLocateFfmpeg:

    @staticmethod
    def _fake_which(mapping):
        return lambda name: mapping.get(name)

    def test_empty_settings_uses_path(self, monkeypatch):
        monkeypatch.setattr(ve.shutil, "which",
                            self._fake_which({"ffmpeg": "/bin/ffmpeg",
                                              "ffprobe": "/bin/ffprobe"}))
        assert ve.locate_ffmpeg({}) == ("/bin/ffmpeg", "/bin/ffprobe")
        assert ve.locate_ffmpeg(None) == ("/bin/ffmpeg", "/bin/ffprobe")

    def test_missing_from_path_yields_none(self, monkeypatch):
        monkeypatch.setattr(ve.shutil, "which", self._fake_which({}))
        assert ve.locate_ffmpeg({}) == (None, None)

    def test_explicit_path_wins_over_path_env(self, monkeypatch, tmp_path):
        real = tmp_path / "myffmpeg.exe"
        real.write_bytes(b"x")
        monkeypatch.setattr(ve.shutil, "which",
                            self._fake_which({"ffmpeg": "/bin/ffmpeg",
                                              "ffprobe": "/bin/ffprobe"}))
        got = ve.locate_ffmpeg({"ffmpeg_path": str(real)})
        assert got[0] == str(real), "设置值必须优先于 PATH"
        assert got[1] == "/bin/ffprobe", "没填的那一半仍走 PATH"

    def test_filled_but_missing_does_not_fall_back(self, monkeypatch, tmp_path):
        """静默回落到 PATH 会让用户以为改生效了，实际跑的是另一个 ffmpeg"""
        monkeypatch.setattr(ve.shutil, "which",
                            self._fake_which({"ffmpeg": "/bin/ffmpeg",
                                              "ffprobe": "/bin/ffprobe"}))
        gone = str(tmp_path / "deleted.exe")
        assert ve.locate_ffmpeg({"ffmpeg_path": gone}) == (None, "/bin/ffprobe")

    def test_whitespace_only_counts_as_unset(self, monkeypatch):
        monkeypatch.setattr(ve.shutil, "which",
                            self._fake_which({"ffmpeg": "/bin/ffmpeg",
                                              "ffprobe": "/bin/ffprobe"}))
        assert ve.locate_ffmpeg({"ffmpeg_path": "   ", "ffprobe_path": ""}) \
            == ("/bin/ffmpeg", "/bin/ffprobe")

    def test_surrounding_whitespace_is_stripped(self, monkeypatch, tmp_path):
        real = tmp_path / "ff.exe"
        real.write_bytes(b"x")
        monkeypatch.setattr(ve.shutil, "which", self._fake_which({}))
        assert ve.locate_ffmpeg({"ffmpeg_path": f"  {real}  "})[0] == str(real)


# ==================== 无 ffmpeg 时的降级行为 ====================

class TestOfflineDegradation:
    """这些分支在目标机器没装 ffmpeg 时就是用户看到的第一屏，必须给得出人话"""

    def test_detect_capabilities_without_path(self):
        cap = ve.detect_capabilities(None)
        assert cap["ok"] is False and cap["reason"]

    def test_detect_capabilities_with_nonexistent_file(self, tmp_path):
        cap = ve.detect_capabilities(str(tmp_path / "nope.exe"))
        assert cap["ok"] is False and cap["reason"]

    def test_test_encoder_without_path(self):
        ok, why = ve.test_encoder(None, "libx264")
        assert ok is False and why

    def test_probe_nonexistent_file(self, tmp_path):
        info = ve.probe(str(tmp_path / "nope.mp4"), "ffprobe")
        assert info["ok"] is False and "不存在" in info["reason"]

    def test_probe_without_ffprobe(self, tmp_path):
        f = tmp_path / "a.mp4"
        f.write_bytes(b"\x00" * 32)
        info = ve.probe(str(f), "")
        assert info["ok"] is False and "ffprobe" in info["reason"]

    def test_probe_empty_path(self):
        assert ve.probe("", "ffprobe")["ok"] is False

    def test_run_enhance_without_ffmpeg_raises(self):
        with pytest.raises(ve.EnhanceError, match="ffmpeg 未定位"):
            ve.run_enhance("a.mp4", "b.mp4", _p(), "", "")

    def test_parse_rate_handles_ffprobe_fractions(self):
        assert ve._parse_rate("25/1") == 25.0
        assert ve._parse_rate("30000/1001") == pytest.approx(29.97, abs=0.01)

    def test_parse_rate_tolerates_garbage(self):
        assert not ve._parse_rate("N/A")
        assert not ve._parse_rate(None)
        assert not ve._parse_rate("0/0")


# ==================== 评测输出解析 ====================

class TestMetricParsing:

    PSNR_LINE = ("[Parsed_psnr_0 @ 0000020f] PSNR y:38.12 u:40.50 v:40.80 "
                 "average:38.60 min:34.20 max:inf")
    SSIM_LINE = ("[Parsed_ssim_0 @ 0000020f] SSIM Y:0.99123 (20.58 dB) "
                 "U:0.99456 (22.64 dB) V:0.99500 (23.01 dB) "
                 "All:0.99234 (21.12 dB)")

    def test_psnr(self):
        assert ve.parse_metric_output("psnr", self.PSNR_LINE) == pytest.approx(38.60)

    def test_ssim(self):
        assert ve.parse_metric_output("ssim", self.SSIM_LINE) == pytest.approx(0.99234)

    def test_vmaf_from_text(self):
        assert ve.parse_metric_output("vmaf", "VMAF score: 93.4567") \
            == pytest.approx(93.4567)

    def test_vmaf_prefers_json_log(self, tmp_path):
        """刮文本不如读 JSON：libvmaf 的文本行格式随版本变过"""
        log = tmp_path / "vmaf.json"
        log.write_text('{"pooled_metrics":{"vmaf":{"score":91.25}}}',
                       encoding="utf-8")
        assert ve.parse_metric_output("vmaf", "VMAF score: 1.0", str(log)) \
            == pytest.approx(91.25)

    def test_vmaf_broken_json_falls_back_to_text(self, tmp_path):
        log = tmp_path / "vmaf.json"
        log.write_text("not json at all", encoding="utf-8")
        assert ve.parse_metric_output("vmaf", "VMAF score: 88.5", str(log)) \
            == pytest.approx(88.5)

    def test_vmaf_missing_log_uses_text(self, tmp_path):
        assert ve.parse_metric_output("vmaf", "VMAF score: 70.0",
                                      str(tmp_path / "absent.json")) \
            == pytest.approx(70.0)

    @pytest.mark.parametrize("kind", ["psnr", "ssim", "vmaf"])
    def test_unparsable_returns_none(self, kind):
        assert ve.parse_metric_output(kind, "ffmpeg 崩了，什么也没输出") is None

    def test_empty_text(self):
        assert ve.parse_metric_output("psnr", "") is None
        assert ve.parse_metric_output("psnr", None) is None

    def test_metric_command_input_order_is_distorted_then_reference(self):
        """0=失真侧(输出) 1=参考侧(源)，搞反了分数没有意义"""
        cmd = ve.build_metric_command("src.mp4", "dst.mp4", "psnr")
        i = cmd.index("-i")
        assert cmd[i + 1] == "dst.mp4"
        assert cmd[i + 2] == "-i" and cmd[i + 3] == "src.mp4"
        assert "[0:v][1:v]psnr" in cmd[cmd.index("-lavfi") + 1]
        assert "-an" in cmd and cmd[-1] == "-"

    def test_vmaf_command_carries_log_path(self, tmp_path):
        cmd = ve.build_metric_command("s", "d", "vmaf", log_path="C:/t/v.json")
        opt = cmd[cmd.index("-lavfi") + 1]
        assert "libvmaf=log_fmt=json:log_path=" in opt

    def test_unknown_metric_raises(self):
        with pytest.raises(ve.EnhanceError, match="未知指标"):
            ve.build_metric_command("s", "d", "ms-ssim")

    def test_scaled_command_prescales_reference_to_output_size(self):
        """输出被放大过时必须先把源放大到同尺寸，否则报 dimension mismatch"""
        cmd = ve.build_scaled_metric_command("s", "d", "psnr", width=1920,
                                             height=1080)
        opt = cmd[cmd.index("-lavfi") + 1]
        assert opt.startswith("[1:v]scale=1920:1080:flags=lanczos[ref];")
        assert opt.endswith("[0:v][ref]psnr")

    def test_scaled_command_requires_dimensions(self):
        with pytest.raises(ve.EnhanceError, match="分辨率"):
            ve.build_scaled_metric_command("s", "d", "psnr", width=0, height=0)

    def test_quote_filter_path_escapes_windows_drive_colon(self):
        """C: 里的冒号在滤镜参数里是选项分隔符，不转义整条链就废了"""
        q = ve._quote_filter_path(r"C:\tmp\vmaf.json")
        assert q == "'C\\:/tmp/vmaf.json'"

    def test_frame_command_seeks_before_input(self):
        """-ss 放 -i 之后会逐帧解码到该点，几分钟的片子要等半分钟"""
        cmd = ve.build_frame_command("v.mp4", 12.5, "o.png")
        assert cmd.index("-ss") < cmd.index("-i")
        assert cmd[cmd.index("-ss") + 1] == "12.5"
        assert cmd[-1] == "o.png"

    def test_frame_command_clamps_negative_timestamp(self):
        cmd = ve.build_frame_command("v.mp4", -5, "o.png")
        assert cmd[cmd.index("-ss") + 1] == "0"

    # ---------- 单帧滤镜预览 ----------

    def test_preview_command_applies_the_filter_chain(self):
        cmd = ve.build_preview_command("v.mp4", 3.0, "o.png", _p(), INFO)
        assert "-vf" in cmd
        chain = cmd[cmd.index("-vf") + 1]
        assert chain == ve.build_filter_chain(_p(), INFO)
        assert cmd[cmd.index("-frames:v") + 1] == "1"
        assert cmd[-1] == "o.png"

    def test_preview_command_still_seeks_before_input(self):
        cmd = ve.build_preview_command("v.mp4", 3.0, "o.png", _p(), INFO)
        assert cmd.index("-ss") < cmd.index("-i")
        assert cmd[cmd.index("-ss") + 1] == "3"

    def test_preview_drops_interpolation_even_when_enabled(self):
        """补帧要先缓冲若干帧才吐出第一帧，留着会把「秒级预览」拖成十几秒"""
        p = _p(interp_on=True)
        assert "minterpolate" in ve.build_filter_chain(p, INFO)
        cmd = ve.build_preview_command("v.mp4", 0, "o.png", p, INFO)
        assert "minterpolate" not in cmd[cmd.index("-vf") + 1]

    def test_preview_does_not_mutate_caller_params(self):
        """调用方传进来的就是 UI 正在用的那份参数，被就地关掉补帧会静默改行为"""
        p = _p(interp_on=True)
        ve.build_preview_command("v.mp4", 0, "o.png", p, INFO)
        assert p["interp_on"] is True

    def test_preview_command_omits_vf_when_everything_is_off(self):
        p = _p(preset="off", color_on=False, denoise_on=False, sharpen_on=False)
        cmd = ve.build_preview_command("v.mp4", 0, "o.png", p, INFO)
        if "-vf" in cmd:
            assert cmd[cmd.index("-vf") + 1] == "format=yuv420p"

    def test_extract_preview_frame_reports_failure_without_ffmpeg(self, tmp_path):
        out = str(tmp_path / "prev.png")
        ok, why = ve.extract_preview_frame("nope.mp4", 0, out, _p(), "", INFO)
        assert ok is False and why and not os.path.exists(out)


# ==================== 历史与摘要 ====================

class TestHistoryAndSummary:

    def test_append_does_not_mutate_input(self):
        """传进来的往往是 settings 缓存里的同一个 list，原地改就没法回滚了"""
        history = [{"src": "old"}]
        snapshot = list(history)
        ve.append_history(history, {"src": "new"})
        assert history == snapshot

    def test_newest_first(self):
        got = ve.append_history([{"src": "old"}], {"src": "new"})
        assert [h["src"] for h in got] == ["new", "old"]

    def test_entry_is_copied_not_aliased(self):
        entry = {"src": "a"}
        got = ve.append_history([], entry)
        entry["src"] = "b"
        assert got[0]["src"] == "a"

    def test_ring_buffer_caps_at_limit(self):
        """历史是「新的在前」，满了以后挤掉的必须是列表末尾那条最旧记录"""
        history = [{"i": ve.HISTORY_LIMIT - 1 - n} for n in range(ve.HISTORY_LIMIT)]
        assert history[-1]["i"] == 0
        got = ve.append_history(history, {"i": "new"})
        assert len(got) == ve.HISTORY_LIMIT
        assert got[0]["i"] == "new"
        assert all(h["i"] != 0 for h in got), "最旧那条应该被挤掉"
        assert history[-1]["i"] == 0, "传入的列表不该被原地改"

    def test_tolerates_empty_inputs(self):
        assert len(ve.append_history(None, None)) == 1

    def test_summarize_mentions_preset_encoder_and_quality(self):
        s = ve.summarize_params(_p())
        assert "标准增强" in s and "libx264" in s and "crf18" in s

    def test_summarize_size_mode_mentions_two_pass(self):
        s = ve.summarize_params(_p(rate_mode="size", target_mb=50))
        assert "50MB" in s and "两遍" in s

    def test_summarize_mentions_upscale(self):
        assert "至1080p" in ve.summarize_params(
            _p(scale_on=True, scale_mode="height", scale_height=1080))
        assert "放大1.5x" in ve.summarize_params(
            _p(scale_on=True, scale_mode="ratio", scale_ratio=1.5))

    def test_summarize_unknown_preset_shows_custom(self):
        assert ve.summarize_params(_p(preset="???")).startswith("自定义")

    def test_summarize_empty(self):
        assert ve.summarize_params({}) == ""
        assert ve.summarize_params(None) == ""


# ==================== 参数表自身的一致性 ====================

class TestCatalogConsistency:
    """下拉项的 value 必须都能被引擎认，否则 UI 选得出来但一跑就报错"""

    @pytest.mark.parametrize("algo", [a for a, _ in ve.DENOISE_ALGOS])
    def test_every_denoise_algo_produces_a_filter(self, algo):
        assert ve._denoise_filter(_p(denoise_algo=algo))

    @pytest.mark.parametrize("algo", [a for a, _ in ve.SHARPEN_ALGOS])
    def test_every_sharpen_algo_produces_a_filter(self, algo):
        assert ve._sharpen_filter(_p(sharpen_algo=algo))

    @pytest.mark.parametrize("algo", [a for a, _ in ve.SCALE_ALGOS])
    def test_every_scale_algo_is_accepted(self, algo):
        f = ve._scale_filter(_p(scale_on=True, scale_mode="height",
                                scale_height=1080, scale_algo=algo), INFO)
        assert f.endswith(f"flags={algo}")

    @pytest.mark.parametrize("name", sorted(ve.ENCODERS))
    def test_every_encoder_spec_is_complete(self, name):
        spec = ve.ENCODERS[name]
        for key in ("label", "quality_flag", "presets", "two_pass", "hw",
                    "crf_hint"):
            assert key in spec, f"{name} 缺 {key}"
        assert spec["quality_flag"] in ("-crf", "-cq")
        assert isinstance(spec["presets"], list) and spec["presets"]
        assert spec["two_pass"] is (not spec["hw"]) or name in ("libsvtav1",)

    def test_default_codec_preset_is_a_software_preset(self):
        """默认档位是 libx264/libx265 那一套；硬编的 p1..p7 与之不通用，靠下面那条
        「非法档位不传」的守卫兜住，切换编码器时 UI 也要重填一次"""
        d = ve.default_params()["codec_preset"]
        assert d in ve.ENCODERS["libx264"]["presets"]
        assert d not in ve.ENCODERS["h264_nvenc"]["presets"]

    @pytest.mark.parametrize("name", sorted(ve.ENCODERS))
    def test_invalid_codec_preset_is_dropped_not_passed_through(self, name):
        """传了编码器不认的 -preset，ffmpeg 会整条命令直接报错退出"""
        spec = ve.ENCODERS[name]
        args = ve._video_encode_args(_p(encoder=name, codec_preset="slow"), INFO)
        if "slow" in spec["presets"]:
            assert args[args.index("-preset") + 1] == "slow"
        else:
            assert "-preset" not in args, name

    def test_only_bm3d_needs_filter_complex(self):
        """其余算法都必须能进单链 -vf，这是批处理链路的前提"""
        for algo, _ in ve.DENOISE_ALGOS:
            f = ve._denoise_filter(_p(denoise_algo=algo))
            assert ("split[" in f) == (algo == "bm3d"), algo

    def test_containers_and_audio_modes_have_labels(self):
        assert all(k and v for k, v in ve.CONTAINERS)
        assert all(k and v for k, v in ve.AUDIO_MODES)

    def test_metrics_catalog(self):
        assert [k for k, _ in ve.METRICS] == ["vmaf", "psnr", "ssim"]

    def test_name_placeholders_all_supported_by_template(self, tmp_path):
        src = tmp_path / "clip.mp4"
        src.write_bytes(b"\x00" * 8)
        tpl = "_".join(ve.NAME_PLACEHOLDERS)
        stem, _ = ve._fill_template(tpl, str(src), INFO, _p(preset="anime"))
        for ph in ve.NAME_PLACEHOLDERS:
            assert ph not in stem, f"{ph} 没被替换: {stem}"
