# -*- coding: utf-8 -*-
"""
统一日志：控制台 + 轮转文件，供 credit_check.py / notify.py 共用。
=====================================================================
README §7 合规边界承诺「保留查询日志备查」，此前脚本只有 print，没有落盘。

日志默认写 ``logs/<name>.log``，5 MB 轮转、保留 5 份，UTF-8 编码。
控制台仍走 stdout/stderr（不影响既有输出格式），文件是额外的一份留痕。

设计约束：
  1. 幂等——重复调用不会叠加 handler（否则同一条日志会写多遍）；
  2. 不改变既有 stdout 输出（调用方仍用 print 做用户可见进度）；
  3. 日志目录不可写时降级为「仅控制台」，不抛异常（巡检不能因为日志挂掉）。
"""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

#: 日志根目录（与本模块同级，便于整目录搬走）
LOG_DIR = Path(__file__).resolve().parent / "logs"

FMT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"
DATEFMT = "%Y-%m-%d %H:%M:%S"
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 5

_MARK = "_credit_logkit_configured"


def setup_logging(
    name: str,
    *,
    level: int = logging.INFO,
    log_dir: Path | None = None,
    max_bytes: int = MAX_BYTES,
    backup_count: int = BACKUP_COUNT,
    console: bool = True,
    console_level: int = logging.WARNING,
) -> logging.Logger:
    """给 ``name`` 配置控制台 + 轮转文件日志，返回该 logger。

    重复调用同一 name 时直接返回已配置的 logger（幂等）。

    控制台默认只输出 WARNING 及以上，且写到 **stderr**：
    脚本本身已用 print 输出用户可见的进度与报告（走 stdout），
    日志若也往 stdout 打 INFO 会污染那份输出（webhook 场景下 stdout 可能被管道消费）。
    """
    logger = logging.getLogger(name)
    if getattr(logger, _MARK, False):
        return logger

    logger.setLevel(level)
    logger.propagate = False  # 避免被 root logger 再写一遍

    formatter = logging.Formatter(FMT, datefmt=DATEFMT)

    if console:
        ch = logging.StreamHandler(sys.stderr)
        ch.setLevel(console_level)
        ch.setFormatter(formatter)
        logger.addHandler(ch)

    target_dir = Path(log_dir) if log_dir is not None else LOG_DIR
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(
            target_dir / f"{name}.log",
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        fh.setLevel(level)
        fh.setFormatter(formatter)
        logger.addHandler(fh)
        logger.log_path = target_dir / f"{name}.log"  # 供调用方回报路径
    except OSError as e:
        # 日志写不了不能拖垮巡检本身
        logger.warning("日志文件不可用（%s），本次仅输出到控制台", e)
        logger.log_path = None

    setattr(logger, _MARK, True)
    return logger


def log_path(name: str, log_dir: Path | None = None) -> Path:
    """返回该 logger 对应的日志文件路径（不创建文件）。"""
    target_dir = Path(log_dir) if log_dir is not None else LOG_DIR
    return target_dir / f"{name}.log"
