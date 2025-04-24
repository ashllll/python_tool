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

# 尝试导入 unarr 库，用于纯 Python 解压
try:
    import unrar  # pip install unrar
except ImportError:
    unrar = None

# 尝试导入 pyunpack 和 patool，增强解压功能
try:
    from pyunpack import Archive
    from easyprocess import EasyProcess
    PYUNPACK_AVAILABLE = True
    logging.info("pyunpack 和 patool 库可用，支持多种格式压缩包解压")
except ImportError:
    PYUNPACK_AVAILABLE = False
    logging.warning("无法导入 pyunpack 或 patool 库，解压功能受限")

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

                # 完成所有文件扫描后，确保关闭最后一个临时文件
                if current_temp_file and current_temp_out and not current_temp_out.closed:
                    final_file = close_temp_file(current_temp_file, current_temp_out, current_segment_start, current_segment_end)
                    if final_file:
                        output_files.append(final_file)

            # 输出处理结果
            if not self._stop_requested:
                if len(output_files) > 1:
                    self.debug.emit(f"生成了 {len(output_files)} 个结果文件，按时间段划分:")
                    for of in output_files:
                        fname = os.path.basename(of)
                        self.debug.emit(f" - {fname}")
                    self.debug.emit("主结果文件将在浏览器中打开。")
                    
                if self._result_truncated:
                    self.warning.emit(f"结果已截断，超过最大限制 {self.max_results} 条。请调整更多关键词或缩小时间范围。")

            # 清理临时文件信息并回收内存
            self._temp_files_info.clear()
            # ==== 内存回收 START ====
            gc.collect()
            # ==== 内存回收 END ====

            # 返回第一个输出文件作为最终结果
            if output_files:
                self.out_path = output_files[0]

            self.finished.emit(self.out_path)
        except Exception as e:
            self.error.emit(f"扫描任务出错: {e}")

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

# --- 新增 DecompressWorker 类用于异步解压 ---
class DecompressWorker(QThread):
    """
    通用解压工作线程，使用pyunpack和patool支持多种压缩格式。
    支持的格式包括：.rar/.zip/.7z/.tar/.tar.gz/.gz等常见压缩格式，
    自动处理嵌套解压，最终通过finished信号返回所有.log文件路径列表。
    """
    progress = pyqtSignal(str)
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(
        self,
        source_path: str,
        is_directory: bool = False,
        parent: Optional[QWidget] = None
    ) -> None:
        super().__init__(parent)
        self.source_path = source_path
        self.is_directory = is_directory

    def run(self) -> None:
        """执行解压任务：先解 archive，再解嵌套 .gz，最后收集 .log 文件。"""
        if not PYUNPACK_AVAILABLE and not unrar:
            self.error.emit("未安装解压库。请执行 pip install pyunpack patool 或 pip install unrar")
            return

        # 1. 临时目录准备
        temp_dir = os.path.join(tempfile.gettempdir(), "log_highlighter_decompress")
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
        os.makedirs(temp_dir, exist_ok=True)

        def _extract_archive(src: str, dst: str) -> None:
            """使用pyunpack或unrar解压文件到指定目录"""
            try:
                if PYUNPACK_AVAILABLE:
                    # pyunpack方式解压
                    Archive(src).extractall(dst)
                    self.progress.emit(f"成功解压 {os.path.basename(src)}")
                elif unrar and src.lower().endswith('.rar'):
                    # 回退到unrar（仅支持rar）
                    unrar.extract(src, dst)
                    self.progress.emit(f"使用unrar成功解压 {os.path.basename(src)}")
                else:
                    self.error.emit(f"无法解压 {os.path.basename(src)}：未安装支持的解压库")
                    return
            except Exception as e:
                logging.error(f"解压 {os.path.basename(src)} 失败: {e}")
                self.error.emit(f"解压 {os.path.basename(src)} 失败: {e}")

        try:
            # 支持目录和单文件两种模式
            if self.is_directory:
                self.progress.emit(f"扫描目录 {os.path.basename(self.source_path)} 中的压缩文件…")
                for root, _, files in os.walk(self.source_path):
                    for fn in files:
                        if fn.lower().endswith(('.rar', '.zip', '.7z', '.tar', '.tar.gz', '.tgz')):
                            src = os.path.join(root, fn)
                            dest_dir = os.path.join(temp_dir, os.path.splitext(fn)[0])
                            os.makedirs(dest_dir, exist_ok=True)
                            self.progress.emit(f"解压 {fn} → {os.path.basename(dest_dir)}")
                            _extract_archive(src, dest_dir)
            else:
                fn = os.path.basename(self.source_path)
                # 检查是否为支持的压缩格式
                if PYUNPACK_AVAILABLE or (fn.lower().endswith('.rar') and unrar):
                    dest_dir = os.path.join(temp_dir, os.path.splitext(fn)[0])
                    os.makedirs(dest_dir, exist_ok=True)
                    self.progress.emit(f"解压 {fn} → {os.path.basename(dest_dir)}")
                    _extract_archive(self.source_path, dest_dir)
                else:
                    self.error.emit(f"不支持的压缩格式: {fn}，请安装pyunpack和patool支持更多格式")
                    return

            # 2. 处理嵌套 .gz 文件（单独的.gz文件）
            for root, _, files in os.walk(temp_dir):
                for fn in files:
                    if fn.lower().endswith('.gz') and not fn.lower().endswith('.tar.gz'):
                        gz_path = os.path.join(root, fn)
                        out_path = os.path.splitext(gz_path)[0]
                        self.progress.emit(f"解压 {fn} → {os.path.basename(out_path)}")
                        try:
                            with gzip.open(gz_path, 'rb') as f_in, open(out_path, 'wb') as f_out:
                                shutil.copyfileobj(f_in, f_out)
                        except Exception as e:
                            logging.error(f"解压 {fn} 失败: {e}")

            # 3. 处理嵌套压缩包（递归解压最多3层）
            depth = 0
            max_depth = 3
            while depth < max_depth:
                found_nested = False
                for root, _, files in os.walk(temp_dir):
                    for fn in files:
                        if fn.lower().endswith(('.rar', '.zip', '.7z', '.tar', '.tar.gz', '.tgz')):
                            src = os.path.join(root, fn)
                            dest_dir = os.path.join(root, os.path.splitext(fn)[0])
                            os.makedirs(dest_dir, exist_ok=True)
                            self.progress.emit(f"解压嵌套文件 {fn} → {os.path.basename(dest_dir)}")
                            _extract_archive(src, dest_dir)
                            found_nested = True
                if not found_nested:
                    break
                depth += 1
                self.progress.emit(f"完成第{depth}层嵌套解压")

            # 4. 收集所有 .log 文件
            logs: List[str] = []
            for root, _, files in os.walk(temp_dir):
                for fn in files:
                    if fn.lower().endswith('.log'):
                        logs.append(os.path.join(root, fn))

            self.progress.emit(f"共找到 {len(logs)} 个日志文件")

            # 5. 发送结果
            self.finished.emit(logs)

        except Exception as e:
            self.error.emit(f"解压过程中出错: {e}")
            logging.error(f"解压过程中出错: {e}")
            # 尝试清理临时目录
            try:
                if os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir)
            except Exception:
                pass

class LogHighlighter(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("日志关键词高亮工具")
        self.resize(1200, 800)
        self.settings_path = "settings.json"
        self.config_path = None
        self.history = {"sources": [], "keywords": [], "cores": os.cpu_count() or 1}
        self.group_colors = {}
        self.custom_keyword_checks = []
        self.worker = None
        self.decompressed_files = []  # 存储解压后的文件路径
        # 初始化参数
        self.config_params = {
            "max_results": CONFIG_DEFAULTS["max_results"],
            "time_range_hours": CONFIG_DEFAULTS["time_range_hours"],
            "chunk_size": CONFIG_DEFAULTS["chunk_size"],
            "thread_timeout": CONFIG_DEFAULTS["thread_timeout"],
            "max_file_size": CONFIG_DEFAULTS["max_file_size"],
            "batch_update_size": CONFIG_DEFAULTS["batch_update_size"]
        }
        self.init_ui()
        QTimer.singleShot(100, self.load_settings)
        # 捕获系统信号优雅退出
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
        atexit.register(self.cleanup_temp_files)

    def signal_handler(self, signum: int, frame: object) -> None:
        """处理系统信号"""
        logging.info(f"收到系统信号 {signum}，正在关闭程序")
        self.close()

    def cleanup_temp_files(self) -> None:
        """清理所有临时文件，并回收内存。"""
        temp_dir = tempfile.gettempdir()
        for f in os.listdir(temp_dir):
            if f.startswith(tempfile.gettempprefix()) and f.endswith('.html'):
                try:
                    os.remove(os.path.join(temp_dir, f))
                    logging.info(f"清理临时文件: {f}")
                except Exception as e:
                    logging.error(f"清理临时文件 {f} 失败: {e}")
        # ==== 内存回收 START ====
        gc.collect()
        # ==== 内存回收 END ====

    def init_ui(self) -> None:
        """初始化用户界面。"""
        mainSplitter = QSplitter(Qt.Vertical)
        self.setCentralWidget(mainSplitter)
        topSplitter = QSplitter(Qt.Horizontal)
        mainSplitter.addWidget(topSplitter)

        # 左侧面板
        left = QWidget()
        ll = QVBoxLayout(left)
        topSplitter.addWidget(left)

        cfg_g = QGroupBox("配置文件 (TOML)")
        cfg_l = QHBoxLayout(cfg_g)
        self.cfg_edit = QLineEdit(readOnly=True)
        self.btn_cfg = QPushButton("选择配置文件")
        self.btn_cfg.clicked.connect(self.select_config)
        cfg_l.addWidget(self.cfg_edit)
        cfg_l.addWidget(self.btn_cfg)
        ll.addWidget(cfg_g)

        src_g = QGroupBox("日志源 (目录/文件)")
        src_l = QHBoxLayout(src_g)
        self.src_list = QListWidget()
        self.src_list.setSelectionMode(QListWidget.ExtendedSelection)
        btns = QVBoxLayout()
        self.btn_add_dir = QPushButton("添加目录")
        self.btn_add_dir.clicked.connect(self.add_directory)
        self.btn_add_file = QPushButton("添加文件")
        self.btn_add_file.clicked.connect(self.add_file)
        self.btn_add_archive = QPushButton("添加压缩包")
        self.btn_add_archive.clicked.connect(self.add_archive)
        self.btn_remove = QPushButton("移除所选")
        self.btn_remove.clicked.connect(self.remove_sources)
        self.btn_clear = QPushButton("清除历史")
        self.btn_clear.clicked.connect(self.clear_history)
        for b in (self.btn_add_dir, self.btn_add_file, self.btn_add_archive, self.btn_remove, self.btn_clear):
            btns.addWidget(b)
        btns.addStretch()
        src_l.addWidget(self.src_list, 4)
        src_l.addLayout(btns, 1)
        ll.addWidget(src_g)

        cpu_g = QGroupBox("CPU 核心数")
        cpu_l = QHBoxLayout(cpu_g)
        cpu_l.addWidget(QLabel("使用核心:"))
        self.spin_cores = QSpinBox()
        maxc = os.cpu_count() or 1
        self.spin_cores.setRange(1, maxc)
        self.spin_cores.setValue(maxc)
        cpu_l.addWidget(self.spin_cores)
        cpu_l.addStretch()
        ll.addWidget(cpu_g)

        # 添加参数设置模块
        params_g = QGroupBox("参数设置")
        params_l = QVBoxLayout(params_g)
        
        # 最大结果数
        max_results_l = QHBoxLayout()
        max_results_label = QLabel("最大结果数:")
        max_results_label.setToolTip("设置每个时间段的最大结果数量，范围: 1000-100000\n影响: 越大内存占用越高，防止结果过多")
        self.spin_max_results = QSpinBox()
        self.spin_max_results.setRange(1000, 100000)
        self.spin_max_results.setValue(self.config_params["max_results"])
        self.spin_max_results.valueChanged.connect(lambda v: self.update_config_param("max_results", v))
        max_results_l.addWidget(max_results_label)
        max_results_l.addWidget(self.spin_max_results)
        max_results_l.addStretch()
        params_l.addLayout(max_results_l)

        # 时间范围（小时）
        time_range_l = QHBoxLayout()
        time_range_label = QLabel("每文件小时数:")
        time_range_label.setToolTip("设置每个 HTML 文件包含的时间范围，范围: 1-24 小时\n影响: 越小生成文件越多，每个文件内容更少")
        self.spin_time_range = QSpinBox()
        self.spin_time_range.setRange(1, 24)
        self.spin_time_range.setValue(self.config_params["time_range_hours"])
        self.spin_time_range.valueChanged.connect(lambda v: self.update_config_param("time_range_hours", v))
        time_range_l.addWidget(time_range_label)
        time_range_l.addWidget(self.spin_time_range)
        time_range_l.addStretch()
        params_l.addLayout(time_range_l)

        # 文件分块大小（字节）
        chunk_size_l = QHBoxLayout()
        chunk_size_label = QLabel("文件分块大小 (KB):")
        chunk_size_label.setToolTip("设置文件读取的分块大小，范围: 128-8192 KB\n影响: 越大读取速度越快，但内存占用越高")
        self.spin_chunk_size = QSpinBox()
        self.spin_chunk_size.setRange(128, 8192)  # 128KB to 8MB
        self.spin_chunk_size.setValue(self.config_params["chunk_size"] // 1024)  # Convert bytes to KB
        self.spin_chunk_size.valueChanged.connect(lambda v: self.update_config_param("chunk_size", v * 1024))
        chunk_size_l.addWidget(chunk_size_label)
        chunk_size_l.addWidget(self.spin_chunk_size)
        chunk_size_l.addStretch()
        params_l.addLayout(chunk_size_l)

        # 线程超时时间（毫秒）
        thread_timeout_l = QHBoxLayout()
        thread_timeout_label = QLabel("线程超时 (ms):")
        thread_timeout_label.setToolTip("设置线程等待超时时间，范围: 1000-10000 毫秒\n影响: 越大取消操作等待时间越长，防止资源未释放")
        self.spin_thread_timeout = QSpinBox()
        self.spin_thread_timeout.setRange(1000, 10000)
        self.spin_thread_timeout.setValue(self.config_params["thread_timeout"])
        self.spin_thread_timeout.valueChanged.connect(lambda v: self.update_config_param("thread_timeout", v))
        thread_timeout_l.addWidget(thread_timeout_label)
        thread_timeout_l.addWidget(self.spin_thread_timeout)
        thread_timeout_l.addStretch()
        params_l.addLayout(thread_timeout_l)

        # 最大文件大小（MB）
        max_file_size_l = QHBoxLayout()
        max_file_size_label = QLabel("最大文件大小 (MB):")
        max_file_size_label.setToolTip("设置可处理的最大文件大小，范围: 100-2048 MB\n影响: 越大可处理文件越大，但内存占用增加")
        self.spin_max_file_size = QSpinBox()
        self.spin_max_file_size.setRange(100, 2048)  # 100MB to 2GB
        self.spin_max_file_size.setValue(self.config_params["max_file_size"] // (1024 * 1024))  # Convert bytes to MB
        self.spin_max_file_size.valueChanged.connect(lambda v: self.update_config_param("max_file_size", v * 1024 * 1024))
        max_file_size_l.addWidget(max_file_size_label)
        max_file_size_l.addWidget(self.spin_max_file_size)
        max_file_size_l.addStretch()
        params_l.addLayout(max_file_size_l)

        # UI 更新批次大小
        batch_update_size_l = QHBoxLayout()
        batch_update_size_label = QLabel("UI 更新批次:")
        batch_update_size_label.setToolTip("设置 UI 进度更新的文件批次大小，范围: 5-50\n影响: 越大 UI 更新频率越低，界面更流畅但反馈延迟")
        self.spin_batch_update_size = QSpinBox()
        self.spin_batch_update_size.setRange(5, 50)
        self.spin_batch_update_size.setValue(self.config_params["batch_update_size"])
        self.spin_batch_update_size.valueChanged.connect(lambda v: self.update_config_param("batch_update_size", v))
        batch_update_size_l.addWidget(batch_update_size_label)
        batch_update_size_l.addWidget(self.spin_batch_update_size)
        batch_update_size_l.addStretch()
        params_l.addLayout(batch_update_size_l)

        params_l.addStretch()
        ll.addWidget(params_g)
        ll.addStretch()

        # 右侧面板
        right = QWidget()
        rl = QVBoxLayout(right)
        topSplitter.addWidget(right)

        grp_g = QGroupBox("关键词分组（可多选）")
        grp_l = QVBoxLayout(grp_g)
        self.grp_scroll = QScrollArea()
        self.grp_scroll.setWidgetResizable(True)
        cont_g = QWidget()
        self.group_layout = QVBoxLayout(cont_g)
        self.grp_scroll.setWidget(cont_g)
        grp_l.addWidget(self.grp_scroll)
        rl.addWidget(grp_g)

        cst_g = QGroupBox("自定义关键词")
        cst_l = QHBoxLayout(cst_g)
        self.keyword_combo = QComboBox(editable=True)
        self.case_box = QCheckBox("区分大小写")
        self.word_box = QCheckBox("全字匹配")
        self.regex_box = QCheckBox("使用正则")
        self.btn_add_kw = QPushButton("添加")
        self.btn_add_kw.clicked.connect(self.add_custom_keyword)
        self.btn_clear_kw = QPushButton("清除勾选")
        self.btn_clear_kw.clicked.connect(self.clear_selected_custom_keywords)
        self.btn_sel_all_kw = QPushButton("全选")
        self.btn_sel_all_kw.clicked.connect(self.select_all_custom_keywords)
        for w in (self.keyword_combo, self.case_box, self.word_box, self.regex_box,
                  self.btn_add_kw, self.btn_clear_kw, self.btn_sel_all_kw):
            cst_l.addWidget(w)
        rl.addWidget(cst_g)
        self.custom_scroll = QScrollArea()
        self.custom_scroll.setWidgetResizable(True)
        cont_c = QWidget()
        self.custom_layout = QVBoxLayout(cont_c)
        self.custom_scroll.setWidget(cont_c)
        rl.addWidget(self.custom_scroll)

        ana_g = QGroupBox("分析控制")
        ana_l = QHBoxLayout(ana_g)
        self.btn_analysis = QPushButton("开始分析")
        self.btn_analysis.clicked.connect(self.analyze_combined_keywords)
        self.btn_cancel = QPushButton("取消分析")
        self.btn_cancel.clicked.connect(self.cancel_analysis)
        self.btn_cancel.setVisible(False)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        ana_l.addWidget(self.btn_analysis)
        ana_l.addWidget(self.btn_cancel)
        ana_l.addWidget(self.progress)
        rl.addWidget(ana_g)
        rl.addStretch()

        dbg_g = QGroupBox("调试输出")
        dbg_l = QVBoxLayout(dbg_g)
        self.debug = QTextEdit(readOnly=True)
        dbg_l.addWidget(self.debug)
        mainSplitter.addWidget(dbg_g)

        topSplitter.setStretchFactor(0, 1)
        topSplitter.setStretchFactor(1, 2)
        mainSplitter.setStretchFactor(0, 3)
        mainSplitter.setStretchFactor(1, 1)

    def update_config_param(self, key: str, value: int) -> None:
        """更新配置参数并保存设置。"""
        self.config_params[key] = value
        self.save_settings()

    def select_config(self) -> None:
        """选择 TOML 配置文件。"""
        cfg, _ = QFileDialog.getOpenFileName(self, "选择配置文件", "", "TOML (*.toml)")
        if cfg:
            try:
                toml.load(cfg)  # 校验配置文件
                self.config_path = cfg
                self.cfg_edit.setText(cfg)
                self.save_settings()
                self.update_group_checkboxes()
            except Exception as e:
                logging.error(f"加载配置文件失败: {e}")
                QMessageBox.critical(self, "配置文件错误", f"加载配置文件失败: {str(e)}")

    # --- 新增 update_group_checkboxes 方法 ---
    def update_group_checkboxes(self) -> None:
        """
        根据加载的 TOML 配置文件更新关键词分组的复选框。
        清除现有的分组复选框，并根据配置文件中的分组重新创建复选框，仅显示组名（去除 'group.' 前缀）。
        """
        # 清除现有的分组复选框
        for i in reversed(range(self.group_layout.count())):
            widget = self.group_layout.itemAt(i).widget()
            if widget:
                self.group_layout.removeWidget(widget)
                widget.deleteLater()

        # 如果有配置文件，加载分组信息
        if self.config_path and os.path.isfile(self.config_path):
            try:
                config = toml.load(self.config_path)
                total_groups = len(config)
                for idx, (group_name, _) in enumerate(config.items()):
                    color = generate_color(idx, total_groups)
                    self.group_colors[group_name] = color
                    # 去除 'group.' 前缀，仅显示组名
                    display_name = group_name.replace("group.", "")
                    cb = QCheckBox(display_name)
                    cb.setProperty("group_name", group_name)  # 保留原始名称以便后续逻辑使用
                    self.group_layout.addWidget(cb)
            except Exception as e:
                logging.error(f"更新分组复选框失败: {e}")
                QMessageBox.critical(self, "分组错误", f"更新分组复选框失败: {str(e)}")
    # --- update_group_checkboxes 方法结束 ---

    def shorten_filename(self, filename: str, max_length: int = 200) -> str:
        """缩短文件名以避免路径过长问题"""
        base, ext = os.path.splitext(filename)
        if len(filename) > max_length:
            base = base[:max_length - len(ext) - 3] + "..."
            return base + ext
        return filename

    # --- 修改 add_directory 方法以支持异步解压 ---
    def add_directory(self) -> None:
        """添加日志目录并异步处理压缩文件解压"""
        d = QFileDialog.getExistingDirectory(self, "添加日志目录")
        if d and d not in self.history["sources"]:
            self.history["sources"].insert(0, d)
            self.src_list.insertItem(0, d)
            self.save_settings()
            # 异步处理目录中的压缩文件
            self.debug.append(f"添加目录: {d}")
            worker = DecompressWorker(d, is_directory=True, parent=self)
            worker.progress.connect(lambda msg: self.debug.append(msg))
            worker.finished.connect(lambda files: (
                self.decompressed_files.extend(files),
                self.debug.append(f"目录 {os.path.basename(d)} 解压完成，新增 {len(files)} 个文件")
            ))
            worker.error.connect(lambda msg: (
                self.debug.append(msg),
                QMessageBox.critical(self, "解压错误", msg)
            ))
            worker.start()
    # --- add_directory 方法结束 ---

    def add_file(self) -> None:
        """添加单个日志文件。"""
        f, _ = QFileDialog.getOpenFileName(self, "添加日志文件", "", "所有文件 (*)")
        if f and f not in self.history["sources"]:
            self.history["sources"].insert(0, f)
            self.src_list.insertItem(0, f)
            self.save_settings()

    # --- 修改 add_archive 方法以支持异步解压 ---
    def add_archive(self) -> None:
        """添加压缩包并异步处理解压"""
        f, _ = QFileDialog.getOpenFileName(self, "添加压缩包", "", 
            "所有支持格式 (*.rar *.zip *.7z *.tar *.tar.gz *.tgz);;RAR (*.rar);;ZIP (*.zip);;7Z (*.7z);;TAR (*.tar *.tar.gz *.tgz)")
        if f and f not in self.history["sources"]:
            self.history["sources"].insert(0, f)
            self.src_list.insertItem(0, f)
            self.save_settings()
            self.debug.append(f"添加压缩包: {f}")
            worker = DecompressWorker(f, is_directory=False, parent=self)
            worker.progress.connect(lambda msg: self.debug.append(msg))
            worker.finished.connect(lambda files: (
                self.decompressed_files.extend(files),
                self.debug.append(f"压缩包 {os.path.basename(f)} 解压完成，新增 {len(files)} 个文件")
            ))
            # ==== 自动触发分析 START ====
            worker.finished.connect(self.on_decompress_finished)
            # ==== 自动触发分析 END ====
            worker.error.connect(lambda msg: (
                self.debug.append(msg),
                QMessageBox.critical(self, "解压错误", msg)
            ))
            worker.start()
    # --- add_archive 方法结束 ---

    def on_decompress_finished(self, logs: List[str]) -> None:
        """解压完成后自动触发关键词分析。"""
        self.debug.append("解压完成，开始自动分析关键词")
        self.file_paths = logs
        self.analyze_combined_keywords()

    def remove_sources(self) -> None:
        """移除选中的日志源。"""
        for it in self.src_list.selectedItems():
            p = it.text()
            self.history["sources"].remove(p)
            self.src_list.takeItem(self.src_list.row(it))
        self.save_settings()

    def clear_history(self) -> None:
        """清除历史记录。"""
        self.history["sources"].clear()
        self.src_list.clear()
        self.save_settings()

    def get_log_files(self) -> List[str]:
        """获取所有日志文件路径，包括处理压缩文件"""
        paths = []
        self.decompressed_files = []  # 清空之前的解压文件列表
        temp_decompress_dir = os.path.join(tempfile.gettempdir(), "log_highlighter_decompress")
        if not os.path.exists(temp_decompress_dir):
            os.makedirs(temp_decompress_dir)

        for src in self.history["sources"]:
            if os.path.isfile(src):
                if src.lower().endswith('.rar') and RARFILE_AVAILABLE:
                    # 解压 .rar 文件到临时目录
                    base_name = os.path.splitext(os.path.basename(src))[0]
                    dest_dir = os.path.join(temp_decompress_dir, self.shorten_filename(base_name))
                    if not os.path.exists(dest_dir):
                        os.makedirs(dest_dir)
                    if self.decompress_rar(src, dest_dir):
                        # 查找并解压 .gz 文件
                        gz_files = self.find_and_decompress_gz(dest_dir)
                        self.decompressed_files.extend(gz_files)
                        # 递归处理内部 .rar 文件
                        rar_files = self.recursive_decompress_rar(dest_dir)
                        self.decompressed_files.extend(rar_files)
                        # 收集解压后的所有文件
                        for root, _, files in os.walk(dest_dir):
                            for f in files:
                                if not f.lower().endswith('.rar') and not f.lower().endswith('.gz'):
                                    paths.append(os.path.join(root, f))
                else:
                    paths.append(src)
            elif os.path.isdir(src):
                # 先处理目录中的 .rar 文件
                for root, _, files in os.walk(src):
                    for f in files:
                        if f.lower().endswith('.rar') and RARFILE_AVAILABLE:
                            rar_path = os.path.join(root, f)
                            base_name = os.path.splitext(f)[0]
                            dest_dir = os.path.join(root, self.shorten_filename(base_name))
                            if not os.path.exists(dest_dir):
                                os.makedirs(dest_dir)
                            if self.decompress_rar(rar_path, dest_dir):
                                gz_files = self.find_and_decompress_gz(dest_dir)
                                self.decompressed_files.extend(gz_files)
                                rar_files = self.recursive_decompress_rar(dest_dir)
                                self.decompressed_files.extend(rar_files)
                
                # 然后收集非压缩文件
                for root, _, files in os.walk(src):
                    for f in files:
                        if not f.lower().endswith('.rar') and not f.lower().endswith('.gz'):
                            paths.append(os.path.join(root, f))
                
        # 确保包含所有解压文件
        for f in self.decompressed_files:
            if f not in paths:
                paths.append(f)
        
        # 排序并返回
        paths = list(set(paths))  # 去重
        paths.sort()
        return paths

    def decompress_rar(self, rar_path: str, dest_dir: str) -> bool:
        """解压 .rar 文件到指定目录"""
        if not RARFILE_AVAILABLE:
            logging.error("rarfile 库不可用，无法解压 .rar 文件")
            self.debug.append("rarfile 库不可用，无法解压 .rar 文件")
            return False
        try:
            with rarfile.RarFile(rar_path) as rf:
                rf.extractall(dest_dir)
            logging.info(f"成功解压 {rar_path} 到 {dest_dir}")
            self.debug.append(f"成功解压 {os.path.basename(rar_path)} 到 {dest_dir}")
            return True
        except Exception as e:
            logging.error(f"解压 {rar_path} 失败: {e}")
            self.debug.append(f"解压 {os.path.basename(rar_path)} 失败: {str(e)}")
            return False

    def decompress_gz(self, gz_path: str, dest_path: str) -> bool:
        """解压 .gz 文件到指定路径"""
        try:
            with gzip.open(gz_path, 'rb') as f_in, open(dest_path, 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)
            logging.info(f"成功解压 {gz_path} 到 {dest_path}")
            self.debug.append(f"成功解压 {os.path.basename(gz_path)} 到 {dest_path}")
            return True
        except Exception as e:
            logging.error(f"解压 {gz_path} 失败: {e}")
            self.debug.append(f"解压 {os.path.basename(gz_path)} 失败: {str(e)}")
            return False

    def find_and_decompress_gz(self, directory: str) -> List[str]:
        """递归查找并解压目录中的所有 .gz 文件"""
        decompressed_paths = []
        for root, _, files in os.walk(directory):
            for f in files:
                if f.endswith('.gz'):
                    gz_path = os.path.join(root, f)
                    dest_path = os.path.splitext(gz_path)[0]
                    self.progress.emit(f"解压 {f} 到 {dest_path}...")
                    if self.decompress_gz(gz_path, dest_path):
                        decompressed_paths.append(dest_path)
        return decompressed_paths

    def recursive_decompress_rar(self, root_dir: str) -> List[str]:
        """递归解压 root_dir 中所有 .rar 并处理内部 .gz"""
        decompressed = []
        for curdir, _, files in os.walk(root_dir):
            for f in files:
                if f.lower().endswith('.rar'):
                    rar_path = os.path.join(curdir, f)
                    dest = os.path.join(curdir, self.shorten_filename(os.path.splitext(f)[0]))
                    os.makedirs(dest, exist_ok=True)
                    self.progress.emit(f"递归解压 {f} 到 {dest}...")
                    if self.decompress_rar(rar_path, dest):
                        gz_files = self.find_and_decompress_gz(dest)
                        decompressed.extend(gz_files)
                        sub_rar_files = self.recursive_decompress_rar(dest)
                        decompressed.extend(sub_rar_files)
        return decompressed

    def analyze_combined_keywords(self) -> None:
        """开始分析日志文件中的关键词。"""
        if not self.config_path or not os.path.isfile(self.config_path):
            QMessageBox.warning(self, "提示", "请选择有效配置文件")
            return
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, "提示", "已有分析正在进行，请稍候")
            return

        self.debug.clear()
        files = self.get_log_files()
        if not files:
            QMessageBox.warning(self, "提示", "无日志文件可分析")
            return

        custom_kws = []
        for cb in self.custom_keyword_checks:
            if cb.property("keyword_obj") and cb.isChecked():
                custom_kws.append(cb.property("keyword_obj"))

        # 2. 收集分组关键词
        group_kws = []
        for i in range(self.group_layout.count()):
            cb = self.group_layout.itemAt(i).widget()
            if isinstance(cb, QCheckBox) and cb.isChecked():
                # toml 中分组格式示例： config[group.name][key] = { key="xxx", annotation="yyy", ... }
                grp = toml.load(self.config_path).get(cb.property("group_name"), {})
                mc = grp.get("match_case", False)
                ww = grp.get("whole_word", False)
                uz = grp.get("use_regex", False)
                color = self.group_colors.get(cb.property("group_name"), "#ffff99")
                for k, v in grp.items():
                    if isinstance(v, dict) and "key" in v:
                        group_kws.append(
                            Keyword(v["key"], v.get("annotation", ""),
                                    mc, ww, uz, color)
                        )

        # 3. 合并去重
        all_kws = custom_kws + group_kws
        # 如果没有任何关键词，就提示并返回
        if not all_kws:
            QMessageBox.warning(self, "提示", "请勾选至少一个自定义关键词或分组关键词")
            return

        # 4. 构建正则表达式
        parts, mapping, raw_list = [], {}, []
        for idx, kw in enumerate(all_kws):
            gp, name = kw.to_group(idx)
            parts.append(gp)
            mapping[name] = {"annotation": kw.annotation, "color": kw.color}
            raw_list.append(kw.raw)

        try:
            combined_re = RE_MODULE.compile("|".join(parts))
        except Exception as e:
            QMessageBox.critical(self, "正则错误", f"正则表达式编译失败：{e}")
            return
        # === 修改点 END ===

        # （下面的逻辑无需改动，直接使用 new combined_re / mapping / raw_list）
        if self.worker and self.worker.isRunning():
            self.cancel_analysis()

        self.btn_analysis.setEnabled(False)
        self.btn_cancel.setVisible(True)
        self.progress.setVisible(True)

        out_dir = CONFIG_DEFAULTS["output_dir"]
        for s in self.history["sources"]:
            if os.path.isdir(s):
                out_dir = s
                break
        if not out_dir and self.history["sources"]:
            out_dir = os.path.dirname(self.history["sources"][0])
        out_path = os.path.join(out_dir, CONFIG_DEFAULTS["output_filename"])

        maxw = self.spin_cores.value()
        self.worker = ScanWorker(files, combined_re, mapping, raw_list, out_path, maxw, self.config_params, parent=self)
        self.worker.error.connect(lambda msg: QMessageBox.critical(self, "扫描错误", msg))
        self.worker.warning.connect(lambda msg: QMessageBox.warning(self, "结果限制", msg))
        self.worker.progress.connect(lambda c, t, f: (
            self.progress.setRange(0, t),
            self.progress.setValue(c),
            self.debug.append(f"[{c}/{t}] 处理: {f}")
        ))
        self.worker.debug.connect(lambda msg: self.debug.append(msg))

        def on_finished(path: str) -> None:
            self.btn_analysis.setEnabled(True)
            self.btn_cancel.setVisible(False)
            self.progress.setVisible(False)
            if os.path.isfile(path):
                webbrowser.open(path)
            if self.worker:
                self.worker.deleteLater()
                self.worker = None

        self.worker.finished.connect(on_finished)
        self.worker.start()

    def cancel_analysis(self) -> None:
        """取消正在进行的分析任务。"""
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.quit()  # 确保线程退出
            if not self.worker.wait(self.config_params["thread_timeout"]):  # 使用用户设置的超时时间
                logging.warning("线程未能在超时时间内停止，可能存在残留任务")
                self.debug.append("取消分析超时，部分任务可能未完成")
            else:
                self.debug.append("分析已取消")
            self.btn_analysis.setEnabled(True)
            self.btn_cancel.setVisible(False)
            self.progress.setVisible(False)
            if self.worker:
                self.worker.deleteLater()
                self.worker = None

    def closeEvent(self, event: 'QCloseEvent') -> None:
        """处理窗口关闭事件，优雅停线程并回收内存。"""
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.quit()
            if not self.worker.wait(self.config_params["thread_timeout"]):
                logging.warning("关闭窗口时线程未能在超时时间内停止")
            if self.worker:
                self.worker.deleteLater()
                self.worker = None
        QCoreApplication.quit()
        # ==== 内存回收 START ====
        gc.collect()
        # ==== 内存回收 END ====
        event.accept()

    def add_custom_keyword(self) -> None:
        """添加自定义关键词"""
        txt = self.keyword_combo.currentText().strip()
        if not txt:
            return
        if txt not in self.history["keywords"]:
            self.history["keywords"].insert(0, txt)
            self.keyword_combo.insertItem(0, txt)
            self.save_settings()
        parts = [p.strip() for p in txt.split('|') if p.strip()]
        tot = len(self.custom_keyword_checks) + len(parts)
        for p in parts:
            idx = len(self.custom_keyword_checks)
            col = generate_color(idx, tot)
            kw = Keyword(p, "[自定义]",
                         self.case_box.isChecked(),
                         self.word_box.isChecked(),
                         self.regex_box.isChecked(),
                         col)
            cb = QCheckBox(p)
            cb.setProperty("keyword_obj", kw)
            self.custom_keyword_checks.append(cb)
            self.custom_layout.addWidget(cb)

    def clear_selected_custom_keywords(self) -> None:
        """清除选中的自定义关键词"""
        for i in reversed(range(len(self.custom_keyword_checks))):
            cb = self.custom_keyword_checks[i]
            if cb.isChecked():
                self.custom_layout.removeWidget(cb)
                cb.deleteLater()
                self.custom_keyword_checks.pop(i)

    def select_all_custom_keywords(self) -> None:
        """选择所有自定义关键词"""
        for cb in self.custom_keyword_checks:
            cb.setChecked(True)

    def load_settings(self) -> None:
        """加载设置"""
        if os.path.exists(self.settings_path):
            try:
                with open(self.settings_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self.config_path = data.get("config_path")
                if self.config_path:
                    self.cfg_edit.setText(self.config_path)
                h = data.get("history", {})
                self.history["sources"] = h.get("sources", [])
                self.history["keywords"] = h.get("keywords", [])
                cores = h.get("cores", os.cpu_count() or 1)
                self.spin_cores.setValue(cores)
                # 加载参数设置
                self.config_params["max_results"] = h.get("max_results", CONFIG_DEFAULTS["max_results"])
                self.spin_max_results.setValue(self.config_params["max_results"])
                self.config_params["time_range_hours"] = h.get("time_range_hours", CONFIG_DEFAULTS["time_range_hours"])
                self.spin_time_range.setValue(self.config_params["time_range_hours"])
                self.config_params["chunk_size"] = h.get("chunk_size", CONFIG_DEFAULTS["chunk_size"])
                self.spin_chunk_size.setValue(self.config_params["chunk_size"] // 1024)
                self.config_params["thread_timeout"] = h.get("thread_timeout", CONFIG_DEFAULTS["thread_timeout"])
                self.spin_thread_timeout.setValue(self.config_params["thread_timeout"])
                self.config_params["max_file_size"] = h.get("max_file_size", CONFIG_DEFAULTS["max_file_size"])
                self.spin_max_file_size.setValue(self.config_params["max_file_size"] // (1024 * 1024))
                self.config_params["batch_update_size"] = h.get("batch_update_size", CONFIG_DEFAULTS["batch_update_size"])
                self.spin_batch_update_size.setValue(self.config_params["batch_update_size"])
                for s in self.history["sources"]:
                    self.src_list.addItem(s)
                for kw in self.history["keywords"]:
                    self.keyword_combo.addItem(kw)
                self.update_group_checkboxes()
                # 增强分组关键词解析
                if self.config_path and os.path.isfile(self.config_path):
                    raw_groups = toml.load(self.config_path).get("groups", {})
                    self.group_mapping: Dict[str, List[str]] = {}
                    for name, entries in raw_groups.items():
                        if isinstance(entries, dict):
                            kws = entries.get("keywords", [])
                        elif isinstance(entries, list):
                            kws = entries
                        elif isinstance(entries, str):
                            kws = [e.strip() for e in entries.split(",") if e.strip()]
                        else:
                            kws = []
                            self.debug.append(f"Unsupported group type for {name}: {type(entries)}")
                        self.group_mapping[name] = kws
                        self.debug.append(f"加载分组关键词 '{name}': {kws}")
            except Exception as e:
                logging.error(f"加载设置失败: {e}")
                QMessageBox.critical(self, "设置错误", f"加载设置失败: {str(e)}")

    def save_settings(self) -> None:
        """保存设置"""
        self.history["cores"] = self.spin_cores.value()
        self.history["max_results"] = self.config_params["max_results"]
        self.history["time_range_hours"] = self.config_params["time_range_hours"]
        self.history["chunk_size"] = self.config_params["chunk_size"]
        self.history["thread_timeout"] = self.config_params["thread_timeout"]
        self.history["max_file_size"] = self.config_params["max_file_size"]
        self.history["batch_update_size"] = self.config_params["batch_update_size"]
        obj = {"config_path": self.config_path, "history": self.history}
        try:
            # 创建备份文件
            if os.path.exists(self.settings_path):
                backup_path = self.settings_path + ".bak"
                with open(self.settings_path, 'r', encoding='utf-8') as f:
                    with open(backup_path, 'w', encoding='utf-8') as bf:
                        bf.write(f.read())
            with open(self.settings_path, 'w', encoding='utf-8') as f:
                json.dump(obj, f, ensure_ascii=False)
        except Exception as e:
            logging.error(f"保存设置失败: {e}")
            QMessageBox.critical(self, "设置错误", f"保存设置失败: {str(e)}")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = LogHighlighter()
    win.show()
    sys.exit(app.exec_())
