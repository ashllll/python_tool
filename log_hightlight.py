#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os
import json
import html
import toml
import webbrowser
import colorsys
import logging
import gc
import signal
import tempfile
import datetime
import atexit
import shutil
import gzip
from typing import List, Dict, Tuple, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QLineEdit, QPushButton, QFileDialog,
    QHBoxLayout, QVBoxLayout, QMessageBox, QGroupBox, QComboBox, QCheckBox,
    QTextEdit, QScrollArea, QProgressBar, QListWidget, QSplitter, QSpinBox
)
from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal, QCoreApplication

# 尝试导入 re2 库，如果失败则回退到 re 模块
try:
    import re2
    RE_MODULE = re2
    logging.info("使用 google-re2 库进行正则表达式匹配")
except ImportError:
    import re
    RE_MODULE = re
    logging.warning("无法导入 google-re2 库，已回退到 Python 内置 re 模块")

# 尝试导入 rarfile 库，用于解压 .rar 文件
try:
    import rarfile
    RARFILE_AVAILABLE = True
    logging.info("rarfile 库可用，支持 .rar 压缩包解压")
except ImportError:
    RARFILE_AVAILABLE = False
    logging.warning("无法导入 rarfile 库，.rar 压缩包解压功能不可用")

# 设置日志记录
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', 
                    handlers=[logging.FileHandler("log_highlighter.log", encoding='utf-8'), logging.StreamHandler()])

# 常量配置（默认值）
CONFIG_DEFAULTS = {
    "output_dir": os.getcwd(),
    "output_filename": "highlight_results",
    "html_style": {
        "font_family": "Consolas",
        "header": "<h2>分析结果（按时间升序）</h2><hr>",
    },
    "batch_update_size": 10,  # 批量更新 UI 的文件数量
    "max_results": 10000,  # 最大结果数量限制
    "chunk_size": 1024 * 1024,  # 文件分块读取大小（1MB）
    "thread_timeout": 5000,  # 线程等待超时时间（毫秒）
    "max_file_size": 1024 * 1024 * 1024,  # 最大文件大小限制（1GB）
    "time_range_hours": 1,  # 每个 HTML 文件包含的时间范围（小时）
    "max_output_files": 100,  # 最大输出文件数量限制
}

def generate_color(index: int, total: int) -> str:
    """
    根据索引和总数生成颜色值。
    
    Args:
        index: 当前索引
        total: 总数
    
    Returns:
        颜色值的十六进制表示
    """
    hue = (index / max(total, 1)) % 1.0
    r, g, b = colorsys.hls_to_rgb(hue, 0.6, 0.5)
    return '#{:02x}{:02x}{:02x}'.format(int(r*255), int(g*255), int(b*255))

class Keyword:
    def __init__(self, raw: str, annotation: str, match_case: bool = False, whole_word: bool = False, use_regex: bool = False, color: str = "#ffff99"):
        self.raw = raw
        self.annotation = annotation
        self.match_case = match_case
        self.whole_word = whole_word
        self.use_regex = use_regex
        self.color = color

    def to_group(self, idx: int) -> Tuple[str, str]:
        """
        将关键词转换为正则表达式组。
        
        Args:
            idx: 关键词索引
        
        Returns:
            正则表达式模式和组名
        """
        pat = self.raw if self.use_regex else RE_MODULE.escape(self.raw)
        if self.whole_word:
            pat = rf'(?<!\w){pat}(?!\w)'
        if not self.match_case:
            pat = f'(?i:{pat})'
        name = f'k{idx}'
        return f'(?P<{name}>{pat})', name

def highlight_line(raw_line: str, combined_re: 're.Pattern', mapping: Dict[str, Dict[str, str]]) -> str:
    """
    高亮日志行中的关键词。
    
    Args:
        raw_line: 原始日志行
        combined_re: 组合的正则表达式
        mapping: 关键词映射信息
    
    Returns:
        高亮后的 HTML 字符串
    """
    res, last = [], 0
    for m in combined_re.finditer(raw_line):
        s, e = m.span()
        res.append(html.escape(raw_line[last:s]))
        nm = m.lastgroup
        info = mapping[nm]
        kw = html.escape(raw_line[s:e])
        ann = html.escape(info['annotation'])
        res.append(f"<span style='background:{info['color']}'>{kw}</span>"
                   f"<span style='color:gray'> ({ann})</span>")
        last = e
    res.append(html.escape(raw_line[last:]))
    return ''.join(res)

def parse_timestamp(line: str) -> datetime.datetime:
    """
    从日志行中解析时间戳，格式为 MM-DD HH:MM:SS.sss。
    
    Args:
        line: 日志行
    
    Returns:
        解析后的时间戳，如果无法解析则返回 datetime.datetime.min
    """
    try:
        if len(line) >= 18:
            ts_str = line[:18]
            # 假设年份为当前年份，如果需要更精确，可以从日志中提取
            current_year = datetime.datetime.now().year
            return datetime.datetime.strptime(f"{current_year}-{ts_str}", "%Y-%m-%d %H:%M:%S.%f")
    except ValueError:
        logging.warning(f"无法解析时间戳: {line[:18] if len(line) >= 18 else line}")
    return datetime.datetime.min

class ScanWorker(QThread):
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)
    warning = pyqtSignal(str)
    debug = pyqtSignal(str)  # 用于传递调试信息

    def __init__(self, file_paths: List[str], combined_re: 're.Pattern', mapping: Dict[str, Dict[str, str]], 
                 raw_list: List[str], out_path: str, max_workers: int, config_params: Dict[str, int], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.file_paths = file_paths
        self.combined_re = combined_re
        self.mapping = mapping
        self.raw_list = raw_list
        self.out_path = out_path
        self.max_workers = max_workers
        self.max_results = config_params["max_results"]
        self.time_range_hours = config_params["time_range_hours"]
        self.max_file_size = config_params["max_file_size"]
        self.chunk_size = config_params["chunk_size"]
        self.max_output_files = CONFIG_DEFAULTS["max_output_files"]
        self._stop_requested = False
        self._processed_files = 0
        self._batch_update_size = config_params["batch_update_size"]
        self._result_truncated = False
        self._executor = None
        self._temp_files_info = {}  # 存储临时文件路径和文件对象的字典

    def stop(self) -> None:
        """停止扫描任务并清理资源。"""
        self._stop_requested = True
        if self._executor:
            self._executor.shutdown(wait=False, cancel_futures=True)
        for info in list(self._temp_files_info.values()):
            temp_out = info.get("file_obj")
            if temp_out and not temp_out.closed:
                try:
                    temp_out.close()
                except Exception as e:
                    logging.error(f"关闭临时文件对象失败: {e}")
        for temp_file in list(self._temp_files_info.keys()):
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except Exception as e:
                    logging.error(f"删除临时文件 {temp_file} 失败: {e}")
        self._temp_files_info.clear()
        gc.collect()
            gc.collect()

    def run(self) -> None:
        """执行扫描任务。"""
        try:
            total = len(self.file_paths)
            entry_count = 0
            time_range_delta = datetime.timedelta(hours=self.time_range_hours)
            current_segment_start = None
            current_segment_end = None
            current_temp_file = None
            current_temp_out = None
            output_files = []

            def scan_file(path: str) -> Tuple[str, List[Tuple[str, str]]]:
                out = []
                fname = os.path.basename(path)
                try:
                    # 检查文件大小
                    file_size = os.path.getsize(path)
                    if file_size > self.max_file_size:
                        logging.warning(f"文件 {path} 超出大小限制 ({file_size} bytes)，已跳过")
                        return fname, out
                    # 完整扫描
                    encodings = ['utf-8', 'gbk', 'gb2312']
                    for enc in encodings:
                        try:
                            with open(path, 'r', encoding=enc) as f:
                                buffer = ""
                                while True:
                                    chunk = f.read(self.chunk_size)
                                    if not chunk:
                                        if buffer:
                                            lines = buffer.splitlines()
                                            for line in lines:
                                                if self.process_line(line, out):
                                                    break
                                        break
                                    buffer += chunk
                                    lines = buffer.splitlines()
                                    buffer = lines[-1] if lines else ""
                                    for line in lines[:-1] if lines else []:
                                        if self.process_line(line, out):
                                            break
                            break
                        except UnicodeDecodeError:
                            continue
                        except Exception as e:
                            logging.error(f"读取文件 {path} 时出错: {e}")
                            break
                except Exception as e:
                    logging.error(f"扫描文件 {path} 时出错: {e}")
                return fname, out

            def dynamic_workers() -> int:
                return min(self.max_workers * 2, len(self.file_paths), 32)

            def get_temp_file_for_segment(start_time: datetime.datetime, end_time: datetime.datetime) -> Tuple[str, 'TextIO']:
                """为每个时间段创建唯一的临时文件"""
                start_str = start_time.strftime("%Y-%m-%d_%H-%M-%S")
                end_str = end_time.strftime("%Y-%m-%d_%H-%M-%S")
                temp_fd, temp_file = tempfile.mkstemp(suffix=f'_{start_str}_to_{end_str}.html', text=True)
                temp_out = os.fdopen(temp_fd, 'w', encoding='utf-8')
                temp_out.write(f'<html><meta charset="utf-8"><body style="font-family:{CONFIG_DEFAULTS["html_style"]["font_family"]}">\n')
                temp_out.write(CONFIG_DEFAULTS["html_style"]["header"] + f"<p>时间范围: {start_str} 至 {end_str}</p>\n")
                return temp_file, temp_out

            def close_temp_file(temp_file: str, temp_out: 'TextIO', start_time: datetime.datetime, end_time: datetime.datetime) -> Optional[str]:
                """关闭临时文件并生成最终 HTML 文件"""
                if temp_out and not temp_out.closed:
                    try:
                        temp_out.close()
                    except Exception as e:
                        logging.error(f"关闭临时文件 {temp_file} 失败: {e}")
                if os.path.exists(temp_file):
                    start_str = start_time.strftime("%Y-%m-%d_%H-%M-%S")
                    end_str = end_time.strftime("%Y-%m-%d_%H-%M-%S")
                    output_filename = f"{self.out_path}_{start_str}_to_{end_str}.html"
                    with open(output_filename, 'w', encoding='utf-8') as outf:
                        with open(temp_file, 'r', encoding='utf-8') as tempf:
                            content = tempf.read()
                            outf.write(content)
                            outf.write('</body></html>')
                    try:
                        os.remove(temp_file)
                    except Exception as e:
                        logging.error(f"删除临时文件 {temp_file} 失败: {e}")
                    return output_filename
                return None

            self._executor = ThreadPoolExecutor(max_workers=dynamic_workers())
            futures = {self._executor.submit(scan_file, p): p for p in self.file_paths}
            completed_count = 0
            all_results = []
            for fut in as_completed(futures):
                if self._stop_requested:
                    self._executor.shutdown(wait=False, cancel_futures=True)
                    break
                try:
                    fname, outs = fut.result()
                    completed_count += 1
                    self._processed_files += 1
                    all_results.extend(outs)
                    # 批量更新 UI
                    if self._processed_files % self._batch_update_size == 0:
                        self.progress.emit(completed_count, total, fname)
                except Exception as e:
                    logging.error(f"处理文件 {futures[fut]} 时出错: {e}")
                    self.error.emit(f"处理文件 {futures[fut]} 时出错: {str(e)}")
            self._executor.shutdown(wait=True)
            self._executor = None

            if not self._stop_requested:
                # 按时间戳排序
                all_results.sort(key=lambda x: parse_timestamp(x[0]))
                # 分段写入结果
                for ts, hl in all_results:
                    if entry_count >= self.max_results and not self._result_truncated:
                        self._result_truncated = True
                        logging.warning("结果数量超出上限，已截断")
                        self.warning.emit(f"结果数量超出上限（{self.max_results}），已截断部分结果")
                        break
                    timestamp = parse_timestamp(ts)
                    if timestamp == datetime.datetime.min:
                        continue  # 跳过无法解析的时间戳
                    if current_segment_start is None or timestamp >= current_segment_end:
                        if current_temp_file and current_temp_out:
                            output_file = close_temp_file(current_temp_file, current_temp_out, current_segment_start, current_segment_end)
                            if output_file:
                                output_files.append(output_file)
                                if len(output_files) >= self.max_output_files:
                                    logging.warning(f"输出文件数量超出上限 ({self.max_output_files})，已停止生成新文件")
                                    self.warning.emit(f"输出文件数量超出上限 ({self.max_output_files})，已停止生成新文件")
                                    break
                        if len(output_files) < self.max_output_files:
                            current_segment_start = timestamp.replace(minute=0, second=0, microsecond=0)
                            current_segment_end = current_segment_start + datetime.timedelta(hours=self.time_range_hours)
                            current_temp_file, current_temp_out = get_temp_file_for_segment(current_segment_start, current_segment_end)
                            self._temp_files_info[current_temp_file] = {"file_obj": current_temp_out, "start_time": current_segment_start, "end_time": current_segment_end}
                    if len(output_files) < self.max_output_files:
                        current_temp_out.write(hl + '<br>\n')
                    entry_count += 1

                # 关闭最后一个临时文件
                if current_temp_file and current_temp_out and len(output_files) < self.max_output_files:
                    output_file = close_temp_file(current_temp_file, current_temp_out, current_segment_start, current_segment_end)
                    if output_file:
                        output_files.append(output_file)
                self._temp_files_info.clear()
                gc.collect()

                # 如果有输出文件，打开第一个文件
                if output_files:
                    webbrowser.open(output_files[0])
                    self.debug.emit(f"已生成 {len(output_files)} 个 HTML 文件，按时间范围分割")
        except Exception as e:
            logging.error(f"扫描过程中出错: {e}")
            self.error.emit(str(e))
        finally:
            if self._executor:
                self._executor.shutdown(wait=False, cancel_futures=True)
                self._executor = None
            for info in list(self._temp_files_info.values()):
                temp_out = info.get("file_obj")
                if temp_out and not temp_out.closed:
                    try:
                        temp_out.close()
                    except Exception as e:
                        logging.error(f"关闭临时文件对象失败: {e}")
            for temp_file in list(self._temp_files_info.keys()):
                if os.path.exists(temp_file):
                    try:
                        os.remove(temp_file)
                    except Exception as e:
                        logging.error(f"删除临时文件 {temp_file} 失败: {e}")
            self._temp_files_info.clear()
            gc.collect()
            self.finished.emit(self.out_path)

    def process_line(self, line: str, out: List[Tuple[str, str]]) -> bool:
        """
        处理单行日志。
        
        Args:
            line: 日志行
            out: 输出结果列表
        
        Returns:
            是否停止处理
        """
        if self._stop_requested:
            return True
        line = line.rstrip('\n')
        if not any(sub in line for sub in self.raw_list):  # 分层匹配：先快速检查
            return False
        if len(line) > 2000:
            line = line[:2000]
        if self.combined_re.search(line):  # 再进行正则匹配
            ts = line[:18] if len(line) >= 18 else line
            hl = highlight_line(line, self.combined_re, self.mapping)
            out.append((ts, hl))
        return False