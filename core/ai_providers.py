# -*- coding: utf-8 -*-
"""AI 厂商注册表：统一各厂商的 OpenAI 兼容接入参数

settings.json 相关键：
- ai_vendor: 当前选用的厂商标识（默认 deepseek）
- ai_api_keys: {厂商标识: API Key}（落盘 DPAPI 加密，见 core.secrets）
- ai_model: 模型名（空则用所选厂商的默认模型）
- forensic_ai_analysis: AI 分析总开关（默认开）

兼容旧配置：deepseek_api_key / deepseek_model（早期仅支持 DeepSeek 时使用）。
API Key 兜底：settings 未配置时读各厂商官方环境变量。
"""

import os

# 厂商注册表：均走 openai SDK 的 OpenAI 兼容接口
# id: settings.json 中的厂商标识
# label: 界面展示名
# base_url: OpenAI 兼容接口地址
# default_model: 官方推荐默认模型（界面可改）
# env_key: 官方推荐的环境变量名（API Key 兜底来源）
AI_PROVIDERS = (
    {"id": "deepseek", "label": "DeepSeek",
     "base_url": "https://api.deepseek.com",
     "default_model": "deepseek-v4-flash",
     "env_key": "DEEPSEEK_API_KEY"},
    {"id": "qwen", "label": "通义千问",
     "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
     "default_model": "qwen3.8-max",
     "env_key": "DASHSCOPE_API_KEY"},
    {"id": "kimi", "label": "Kimi（月之暗面）",
     "base_url": "https://api.moonshot.cn/v1",
     "default_model": "kimi-k3",
     "env_key": "MOONSHOT_API_KEY"},
    {"id": "zhipu", "label": "智谱 GLM",
     "base_url": "https://open.bigmodel.cn/api/paas/v4",
     "default_model": "glm-5.2",
     "env_key": "ZHIPUAI_API_KEY"},
    {"id": "openai", "label": "OpenAI GPT",
     "base_url": "https://api.openai.com/v1",
     "default_model": "gpt-4o-mini",
     "env_key": "OPENAI_API_KEY"},
    {"id": "gemini", "label": "Google Gemini",
     "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
     "default_model": "gemini-2.5-flash",
     "env_key": "GEMINI_API_KEY"},
)

DEFAULT_VENDOR = "deepseek"


def get_provider(vendor_id: str) -> dict:
    """按厂商标识取注册信息，未知名/空值回退 DeepSeek"""
    for p in AI_PROVIDERS:
        if p["id"] == vendor_id:
            return p
    for p in AI_PROVIDERS:
        if p["id"] == DEFAULT_VENDOR:
            return p
    return {}


def resolve_ai_config(settings: dict) -> dict:
    """从 settings（已解密的明文配置）解析完整的 AI 调用配置

    返回 {"vendor","label","base_url","api_key","model","env_key"}。
    API Key 优先级：ai_api_keys[厂商] > 旧键 deepseek_api_key（仅 DeepSeek）
    > 厂商官方环境变量；api_key 可能为空串（调用方负责友好报错）。
    """
    settings = settings if isinstance(settings, dict) else {}
    vendor = str(settings.get("ai_vendor") or DEFAULT_VENDOR).strip()
    provider = get_provider(vendor)

    keys = settings.get("ai_api_keys")
    keys = keys if isinstance(keys, dict) else {}
    api_key = str(keys.get(provider["id"]) or "").strip()
    if not api_key and provider["id"] == "deepseek":
        # 兼容旧版单厂商配置
        api_key = str(settings.get("deepseek_api_key") or "").strip()
    if not api_key:
        api_key = os.environ.get(provider["env_key"], "").strip()

    model = str(settings.get("ai_model") or "").strip()
    if not model and provider["id"] == "deepseek":
        model = str(settings.get("deepseek_model") or "").strip()
    if not model:
        model = provider["default_model"]

    return {
        "vendor": provider["id"],
        "label": provider["label"],
        "base_url": provider["base_url"],
        "api_key": api_key,
        "model": model,
        "env_key": provider["env_key"],
    }
