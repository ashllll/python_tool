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

def generate_color(index, total):
    hue = (index / max(total, 1)) % 1.0
    r, g, b = colorsys.hls_to_rgb(hue, 0.6, 0.5)
    return '#{:02x}{:02x}{:02x}'.format(int(r*255), int(g*255), int(b*255))

class Keyword:
    def __init__(self, raw, annotation, match_case=False, whole_word=False, use_regex=False, color="#ffff99"):
        self.raw = raw
        self.annotation = annotation
        self.match_case = match_case
        self.whole_word = whole_word
        self.use_regex = use_regex
        self.color = color

    def to_group(self, idx):
        pat = self.raw if self.use_regex else RE_MODULE.escape(self.raw)
        if self.whole_word:
            pat = rf'(?<!\w){pat}(?!\w)'
        if not self.match_case:
            pat = f'(?i:{pat})'
        name = f'k{idx}'
        return f'(?P<{name}>{pat})', name

def highlight_line(raw_line, combined_re, mapping):
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

def parse_timestamp(line):
    """从日志行中解析时间戳，格式为 MM-DD HH:MM:SS.sss"""
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

    def __init__(self, file_paths, combined_re, mapping, raw_list, out_path, max_workers, config_params, parent=None):
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

    def stop(self):
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

    def run(self):
        try:
            total = len(self.file_paths)
            entry_count = 0
            time_range_delta = datetime.timedelta(hours=self.time_range_hours)
            current_segment_start = None
            current_segment_end = None
            current_temp_file = None
            current_temp_out = None
            output_files = []

            def scan_file(path):
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

            def dynamic_workers():
                return min(self.max_workers * 2, len(self.file_paths), 32)

            def get_temp_file_for_segment(start_time, end_time):
                """为每个时间段创建唯一的临时文件"""
                start_str = start_time.strftime("%Y-%m-%d_%H-%M-%S")
                end_str = end_time.strftime("%Y-%m-%d_%H-%M-%S")
                temp_fd, temp_file = tempfile.mkstemp(suffix=f'_{start_str}_to_{end_str}.html', text=True)
                temp_out = os.fdopen(temp_fd, 'w', encoding='utf-8')
                temp_out.write(f'<html><meta charset="utf-8"><body style="font-family:{CONFIG_DEFAULTS["html_style"]["font_family"]}">\n')
                temp_out.write(CONFIG_DEFAULTS["html_style"]["header"] + f"<p>时间范围: {start_str} 至 {end_str}</p>\n")
                return temp_file, temp_out

            def close_temp_file(temp_file, temp_out, start_time, end_time):
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
            self.finished.emit(self.out_path)

    def process_line(self, line, out):
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
        # 捕获系统信号以优雅退出
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
        # 注册退出时清理临时文件
        atexit.register(self.cleanup_temp_files)

    def signal_handler(self, signum, frame):
        logging.info(f"收到系统信号 {signum}，正在关闭程序")
        self.close()

    def cleanup_temp_files(self):
        """清理所有临时文件"""
        temp_dir = tempfile.gettempdir()
        for f in os.listdir(temp_dir):
            if f.startswith(tempfile.gettempprefix()) and f.endswith('.html'):
                try:
                    os.remove(os.path.join(temp_dir, f))
                    logging.info(f"清理临时文件: {f}")
                except Exception as e:
                    logging.error(f"清理临时文件 {f} 失败: {e}")

    def init_ui(self):
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

    def update_config_param(self, key, value):
        self.config_params[key] = value
        self.save_settings()

    def select_config(self):
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
    def update_group_checkboxes(self) -> None:
    """
    根据加载的 TOML 配置文件更新关键词分组的复选框。
    清除现有的分组复选框，并根据配置文件中的分组重新创建复选框。
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
            for idx, (group_name, group_data) in enumerate(config.items()):
                color = generate_color(idx, total_groups)
                self.group_colors[group_name] = color
                display_text = f"{group_name} ({len(group_data) - sum(1 for k in group_data if k in ('match_case', 'whole_word', 'use_regex'))} 关键词)"
                cb = QCheckBox(display_text)
                cb.setProperty("group_name", group_name)
                self.group_layout.addWidget(cb)
        except Exception as e:
            logging.error(f"更新分组复选框失败: {e}")
            QMessageBox.critical(self, "分组错误", f"更新分组复选框失败: {str(e)}")
    
    def shorten_filename(self, filename, max_length=200):
        """缩短文件名以避免路径过长问题"""
        base, ext = os.path.splitext(filename)
        if len(filename) > max_length:
            base = base[:max_length - len(ext) - 3] + "..."
            return base + ext
        return filename

    def decompress_rar(self, rar_path, dest_dir):
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

    def decompress_gz(self, gz_path, dest_path):
        """解压 .gz 文件到指定路径"""
        try:
            with gzip.open(gz_path, 'rb') as f_in:
                with open(dest_path, 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)
            logging.info(f"成功解压 {gz_path} 到 {dest_path}")
            self.debug.append(f"成功解压 {os.path.basename(gz_path)} 到 {dest_path}")
            return True
        except Exception as e:
            logging.error(f"解压 {gz_path} 失败: {e}")
            self.debug.append(f"解压 {os.path.basename(gz_path)} 失败: {str(e)}")
            return False

    def find_and_decompress_gz(self, directory):
        """递归查找并解压目录中的所有 .gz 文件"""
        decompressed_paths = []
        for root, _, files in os.walk(directory):
            for f in files:
                if f.endswith('.gz'):
                    gz_path = os.path.join(root, f)
                    dest_path = os.path.splitext(gz_path)[0]  # 去掉 .gz 后缀
                    if self.decompress_gz(gz_path, dest_path):
                        decompressed_paths.append(dest_path)
        return decompressed_paths

    def add_directory(self):
        d = QFileDialog.getExistingDirectory(self, "添加日志目录")
        if d and d not in self.history["sources"]:
            self.history["sources"].insert(0, d)
            self.src_list.insertItem(0, d)
            self.save_settings()
            # 扫描首层 .rar 文件并解压
            if RARFILE_AVAILABLE:
                try:
                    for f in os.listdir(d):
                        if f.endswith('.rar'):
                            rar_path = os.path.join(d, f)
                            # 创建同名文件夹，处理长文件名
                            base_name = os.path.splitext(f)[0]
                            dest_dir = os.path.join(d, self.shorten_filename(base_name))
                            if not os.path.exists(dest_dir):
                                os.makedirs(dest_dir)
                            if self.decompress_rar(rar_path, dest_dir):
                                # 查找并解压 .gz 文件
                                gz_files = self.find_and_decompress_gz(dest_dir)
                                self.decompressed_files.extend(gz_files)
                except Exception as e:
                    logging.error(f"扫描目录 {d} 中的 .rar 文件失败: {e}")
                    self.debug.append(f"扫描目录 {d} 中的 .rar 文件失败: {str(e)}")
            else:
                self.debug.append("rarfile 库不可用，跳过 .rar 文件解压")

    def add_file(self):
        f, _ = QFileDialog.getOpenFileName(self, "添加日志文件", "", "所有文件 (*)")
        if f and f not in self.history["sources"]:
            self.history["sources"].insert(0, f)
            self.src_list.insertItem(0, f)
            self.save_settings()

    def add_archive(self):
        f, _ = QFileDialog.getOpenFileName(self, "添加压缩包", "", "RAR 文件 (*.rar)")
        if f and f not in self.history["sources"]:
            self.history["sources"].insert(0, f)
            self.src_list.insertItem(0, f)
            self.save_settings()

    def remove_sources(self):
        for it in self.src_list.selectedItems():
            p = it.text()
            self.history["sources"].remove(p)
            self.src_list.takeItem(self.src_list.row(it))
        self.save_settings()

    def clear_history(self):
        self.history["sources"].clear()
        self.src_list.clear()
        self.save_settings()

    def recursive_decompress_rar(self, root_dir):
        """递归解压 root_dir 中所有 .rar 并处理内部 .gz"""
        decompressed = []
        for curdir, _, files in os.walk(root_dir):
            for f in files:
                if f.lower().endswith('.rar'):
                    rar_path = os.path.join(curdir, f)
                    dest = os.path.join(curdir, self.shorten_filename(os.path.splitext(f)[0]))
                    os.makedirs(dest, exist_ok=True)
                    if self.decompress_rar(rar_path, dest):
                        gz_files = self.find_and_decompress_gz(dest)
                        decompressed.extend(gz_files)
                        sub_rar_files = self.recursive_decompress_rar(dest)
                        decompressed.extend(sub_rar_files)
        return decompressed

    def get_log_files(self):
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

    def analyze_combined_keywords(self):
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

        kws = [cb.property("keyword_obj") for cb in self.custom_keyword_checks
               if cb.property("keyword_obj") and cb.isChecked()]
        for i in range(self.group_layout.count()):
            cb = self.group_layout.itemAt(i).widget()
            if isinstance(cb, QCheckBox) and cb.isChecked():
                grp = toml.load(self.config_path).get(cb.property("group_name"), {})
                mc, ww, uz = grp.get("match_case", False), grp.get("whole_word", False), grp.get("use_regex", False)
                color = self.group_colors.get(cb.property("group_name"), "#ffff99")
                for k, v in grp.items():
                    if k in ("match_case", "whole_word", "use_regex"):
                        continue
                    if isinstance(v, dict) and "key" in v:
                        kws.append(Keyword(v["key"], v.get("annotation", ""), mc, ww, uz, color))
        if not kws:
            QMessageBox.warning(self, "提示", "请勾选至少一个关键词")
            return

        parts, mapping, raw_list = [], {}, []
        for idx, kw in enumerate(kws):
            gp, name = kw.to_group(idx)
            parts.append(gp)
            mapping[name] = {"annotation": kw.annotation, "color": kw.color}
            raw_list.append(kw.raw)
        combined_re = RE_MODULE.compile("|".join(parts))

        if self.worker and self.worker.isRunning():
            self.cancel_analysis()

        self.debug.clear()
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

        def on_finished(path):
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

    def cancel_analysis(self):
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

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.quit()
            if not self.worker.wait(self.config_params["thread_timeout"]):
                logging.warning("关闭窗口时线程未能在超时时间内停止")
            if self.worker:
                self.worker.deleteLater()
                self.worker = None
        QCoreApplication.quit()
        event.accept()

    def add_custom_keyword(self):
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

    def clear_selected_custom_keywords(self):
        """清除选中的自定义关键词"""
        for i in reversed(range(len(self.custom_keyword_checks))):
            cb = self.custom_keyword_checks[i]
            if cb.isChecked():
                self.custom_layout.removeWidget(cb)
                cb.deleteLater()
                self.custom_keyword_checks.pop(i)

    def select_all_custom_keywords(self):
        """选择所有自定义关键词"""
        for cb in self.custom_keyword_checks:
            cb.setChecked(True)

    def load_settings(self):
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
            except Exception as e:
                logging.error(f"加载设置失败: {e}")
                QMessageBox.critical(self, "设置错误", f"加载设置失败: {str(e)}")

    def save_settings(self):
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
