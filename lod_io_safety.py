"""
LOD I/O 安全辅助模块

提供 lod-meta.json 的安全加载与 LOD key 校验，供 loader / calculator 共用
（单一真相源，避免安全逻辑在多个文件重复）。

对应人工检视意见：
- 路径校验：realpath 解析（消解符号链接 + `..` 穿越）+ 可选 allowed_base_dir 约束
  + 文件大小限制（防畸形/超大文件导致 OOM）
- LOD key 安全转换：跳过畸形 key，不因单个坏 key 让整体加载崩溃
"""
import os
import json
import logging
from typing import Optional

logger = logging.getLogger("lod_io_safety")

# lod-meta.json 大小上限：实测最大 ~2.34MB (syp_lod_0610)，
# 留足余量到 100MB 以容纳更大场景，同时防止畸形/超大文件导致内存爆掉
DEFAULT_MAX_META_BYTES = 100 * 1024 * 1024  # 100 MB


def safe_load_meta(
    meta_path,
    allowed_base_dir: Optional[str] = None,
    max_bytes: int = DEFAULT_MAX_META_BYTES,
) -> dict:
    """安全加载 lod-meta.json

    Args:
        meta_path: lod-meta.json 路径
        allowed_base_dir: 若指定，解析后的真实路径必须位于此目录内（防路径穿越）。
                          None = 不限制（本地 CLI 默认；嵌入服务/接受外部路径时应传入）
        max_bytes: 文件大小上限，超过则拒绝加载

    Returns:
        解析后的 meta dict

    Raises:
        FileNotFoundError: 文件不存在
        PermissionError:   解析后路径不在 allowed_base_dir 内
        ValueError:        文件超过大小限制 / JSON 解析失败
    """
    # 1. realpath 解析：消解符号链接与 `..`，得到规范真实路径
    real_path = os.path.realpath(str(meta_path))

    if not os.path.isfile(real_path):
        raise FileNotFoundError(f"lod-meta.json not found: {real_path}")

    # 2. 可选：约束在允许目录内（防路径穿越读取敏感文件）
    if allowed_base_dir is not None:
        real_base = os.path.realpath(str(allowed_base_dir))
        try:
            common = os.path.commonpath([real_path, real_base])
        except ValueError:
            # 不同盘符（Windows）→ 必不在允许目录内
            raise PermissionError(
                f"Path outside allowed dir (different drive): {real_path}"
            )
        if common != real_base:
            raise PermissionError(
                f"Path traversal blocked: {real_path} not under {real_base}"
            )

    # 3. 文件大小限制
    size = os.path.getsize(real_path)
    if size > max_bytes:
        raise ValueError(
            f"lod-meta.json too large: {size} bytes > limit {max_bytes} bytes"
        )

    # 4. 加载 JSON（显式 utf-8，避免 Windows 平台默认 GBK 误读）
    try:
        with open(real_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"Malformed lod-meta.json ({real_path}): {e}") from e


def safe_int_key(k) -> Optional[int]:
    """安全地将 LOD key 转为非负 int

    Returns:
        转换成功返回 int；畸形/非数字/负数 key 返回 None（调用方应跳过并记录）
    """
    # JSON object key 恒为 str；兼容极端情况下已是 int 的 key（排除 bool）
    if isinstance(k, int) and not isinstance(k, bool):
        return k if k >= 0 else None
    if isinstance(k, str):
        s = k.strip()
        if s.isdigit():  # 仅纯数字（自动排除负号/小数点/空串/科学计数法）
            return int(s)
    return None


def safe_count(v) -> Optional[int]:
    """从 LOD 值中安全提取 splat count

    支持两种格式：
        {'count': N, ...}  → 取 N
        N (int/float)      → 直接用

    splat count 是整数语义：接受 int 与整数值 float（如 500.0），
    但**拒绝**非整数 float（如 1.9）——静默 int() 截断 = 数据损坏（砚砚 Finding 3）。

    Returns:
        非负 int count；畸形/非整数/负数返回 None（调用方应跳过并记录）
    """
    if isinstance(v, dict) and "count" in v:
        c = v["count"]
    elif isinstance(v, (int, float)) and not isinstance(v, bool):
        c = v
    else:
        return None
    if isinstance(c, bool):
        return None
    if isinstance(c, int):
        return c if c >= 0 else None
    if isinstance(c, float):
        # 仅接受整数值 float（500.0 ✓ / 1.9 ✗），杜绝静默截断
        if c.is_integer() and c >= 0:
            return int(c)
        return None
    return None
