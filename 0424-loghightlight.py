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
import re
from typing import List, Dict, Tuple, Optional
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QLineEdit, QPushButton, QFileDialog,
    QHBoxLayout, QVBoxLayout, QMessageBox, QGroupBox, QComboBox, QCheckBox,
    QTextEdit, QScrollArea, QProgressBar, QListWidget, QSplitter, QSpinBox
)
from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal, QCoreApplication

# 日志记录配置
logging.basicConfig(level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler("log_highlighter.log", encoding='utf-8'),
              logging.StreamHandler()])

# 默认配置常量
CONFIG_DEFAULTS = {
    "output_dir": os.getcwd(),
    "output_filename": "highlight_results",
    "html_style": {
        "font_family": "Consolas",
        "header": "<h2>分析结果（按时间升序）</h2><hr>",
    },
    "batch_update_size": 10,
    "max_results": 10000,
    "chunk_size": 1024 * 1024,
    "thread_timeout": 5000,
    "max_file_size": 1024 * 1024 * 1024,
    "time_range_hours": 1,
    "max_output_files": 100,
}

def generate_color(index: int, total: int) -> str:
    hue = (index / max(total, 1)) % 1.0
    r, g, b = colorsys.hls_to_rgb(hue, 0.6, 0.5)
    return '#{:02x}{:02x}{:02x}'.format(int(r*255), int(g*255), int(b*255))

class Keyword:
    """关键词定义与正则组装"""
    def __init__(self, raw: str, annotation: str,
                 match_case=False, whole_word=False, use_regex=False, color="#ffff99"):
        self.raw = raw
        self.annotation = annotation
        self.match_case = match_case
        self.whole_word = whole_word
        self.use_regex = use_regex
        self.color = color

    def to_group(self, idx: int) -> Tuple[str, str]:
        pat = self.raw if self.use_regex else re.escape(self.raw)
        if self.whole_word:
            pat = rf'(?<!\w){pat}(?!\w)'
        if not self.match_case:
            pat = f'(?i:{pat})'
        name = f'k{idx}'
        return f'(?P<{name}>{pat})', name

def parse_timestamp(line: str) -> datetime.datetime:
    """解析日志行前 18 字符为 MM-DD HH:MM:SS.sss"""
    try:
        if len(line) >= 18:
            ts_str = line[:18]
            year = datetime.datetime.now().year
            return datetime.datetime.strptime(f"{year}-{ts_str}", "%Y-%m-%d %H:%M:%S.%f")
    except ValueError:
        logging.warning(f"无法解析时间戳: {line[:18]}")
    return datetime.datetime.min

class DecompressWorker(QThread):
    """异步解压支持多格式压缩包"""
    progress = pyqtSignal(str)
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, source_path: str, is_directory=False, parent: Optional[QWidget]=None):
        super().__init__(parent)
        self.source_path = source_path
        self.is_directory = is_directory

    def run(self):
        temp_dir = os.path.join(tempfile.gettempdir(), "log_highlighter_decompress")
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
        os.makedirs(temp_dir, exist_ok=True)

        def _extract(src, dst):
            try:
                from pyunpack import Archive
                Archive(src).extractall(dst)
                self.progress.emit(f"解压 {os.path.basename(src)} → {dst}")
            except Exception:
                if src.lower().endswith('.rar'):
                    try:
                        import rarfile
                        with rarfile.RarFile(src) as rf:
                            rf.extractall(dst)
                        self.progress.emit(f"解压 (rarfile) {os.path.basename(src)} → {dst}")
                    except Exception as e:
                        self.error.emit(f"解压 {src} 失败: {e}")
                        return False
                else:
                    self.error.emit(f"不支持的压缩格式: {src}")
                    return False
            return True

        try:
            if self.is_directory:
                for root, _, files in os.walk(self.source_path):
                    for fn in files:
                        if fn.lower().endswith(('.rar','.zip','.7z','.tar','.gz','.tgz')):
                            src = os.path.join(root, fn)
                            dst = os.path.join(temp_dir, os.path.splitext(fn)[0])
                            os.makedirs(dst, exist_ok=True)
                            _extract(src, dst)
            else:
                fn = os.path.basename(self.source_path)
                dst = os.path.join(temp_dir, os.path.splitext(fn)[0])
                os.makedirs(dst, exist_ok=True)
                _extract(self.source_path, dst)

            # 处理嵌套 .gz
            for root, _, files in os.walk(temp_dir):
                for fn in files:
                    if fn.lower().endswith('.gz') and not fn.lower().endswith('.tar.gz'):
                        gz = os.path.join(root, fn)
                        out = gz[:-3]
                        with gzip.open(gz,'rb') as f_in, open(out,'wb') as f_out:
                            shutil.copyfileobj(f_in,f_out)
                        self.progress.emit(f"解压 {fn} → {out}")

            # 递归嵌套格式（最多3层）
            for _ in range(3):
                found = False
                for root, _, files in os.walk(temp_dir):
                    for fn in files:
                        if fn.lower().endswith(('.rar','.zip','.7z','.tar','.tgz')):
                            src = os.path.join(root, fn)
                            dst = os.path.join(root, os.path.splitext(fn)[0])
                            os.makedirs(dst, exist_ok=True)
                            _extract(src, dst)
                            found = True
                if not found:
                    break

            # 收集 .log
            logs = []
            for root, _, files in os.walk(temp_dir):
                for fn in files:
                    if fn.lower().endswith('.log'):
                        logs.append(os.path.join(root, fn))
            self.finished.emit(logs)
        except Exception as e:
            self.error.emit(f"解压错误: {e}")

class KeywordScanThread(QThread):
    """多线程关键词扫描：对单个关键词遍历所有日志文件"""
    result_ready = pyqtSignal(str, dict)  # keyword -> {file:{line:{content, matches}}}
    def __init__(self, keyword: str, log_files: List[str], parent=None):
        super().__init__(parent)
        self.keyword = keyword
        self.log_files = list(log_files)
        self._active = True

    def run(self):
        pattern = re.compile(self.keyword)
        results: Dict[str, Dict[int, dict]] = {}
        for fp in self.log_files:
            if not self._active: break
            if not os.path.isfile(fp): continue
            try:
                with open(fp,'r',encoding='utf-8',errors='ignore') as f:
                    ln = 0
                    for line in f:
                        if not self._active: break
                        ln += 1
                        for m in pattern.finditer(line):
                            results.setdefault(fp,{}).setdefault(ln,{"content":line.rstrip("\n"),"matches":[]})
                            results[fp][ln]["matches"].append((m.start(),m.end()))
            except Exception as e:
                logging.error(f"扫描文件 {fp} 错误: {e}")
        if self._active:
            self.result_ready.emit(self.keyword, results)

    def stop(self):
        self._active = False

class LogHighlighter(QMainWindow):
    """主窗口：保留原功能，引入 KeywordScanThread 取代 ScanWorker"""
    def __init__(self):
        super().__init__()
        self.setWindowTitle("日志关键词高亮工具")
        self.resize(1200,800)

        # 状态
        self.settings_path = "settings.json"
        self.config_path = None
        self.history = {"sources":[], "keywords":[], "cores":os.cpu_count() or 1}
        self.group_colors: Dict[str,str] = {}
        self.custom_checks: List[QCheckBox] = []
        self.decompressed_files: List[str] = []
        self.config_params = {
            "max_results":CONFIG_DEFAULTS["max_results"],
            "time_range_hours":CONFIG_DEFAULTS["time_range_hours"],
            "chunk_size":CONFIG_DEFAULTS["chunk_size"],
            "thread_timeout":CONFIG_DEFAULTS["thread_timeout"],
            "max_file_size":CONFIG_DEFAULTS["max_file_size"],
            "batch_update_size":CONFIG_DEFAULTS["batch_update_size"],
        }

        # 增量分析状态
        self.previous_keywords: set = set()
        self.cached_results: Dict[str, dict] = {}
        self.current_threads: Dict[str, KeywordScanThread] = {}

        self.init_ui()
        QTimer.singleShot(100, self.load_settings)
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
        atexit.register(self.cleanup_temp_files)

    def signal_handler(self, signum, frame):
        logging.info(f"收到系统信号 {signum}，退出")
        self.close()

    def cleanup_temp_files(self):
        tmp = tempfile.gettempdir()
        for f in os.listdir(tmp):
            if f.startswith(tempfile.gettempprefix()) and f.endswith('.html'):
                try: os.remove(os.path.join(tmp,f))
                except: pass
        gc.collect()

    def init_ui(self):
        mainSpl = QSplitter(Qt.Vertical)
        self.setCentralWidget(mainSpl)
        topSpl = QSplitter(Qt.Horizontal)
        mainSpl.addWidget(topSpl)

        # 左侧：配置文件 & 源
        left = QWidget(); ll=QVBoxLayout(left)
        topSpl.addWidget(left)

        # TOML 配置
        cfg_g=QGroupBox("配置文件 (TOML)"); cfg_l=QHBoxLayout(cfg_g)
        self.cfg_edit=QLineEdit(readOnly=True); btn_cfg=QPushButton("选择配置文件")
        btn_cfg.clicked.connect(self.select_config)
        cfg_l.addWidget(self.cfg_edit); cfg_l.addWidget(btn_cfg); ll.addWidget(cfg_g)

        # 日志源
        src_g=QGroupBox("日志源 (目录/文件)"); src_l=QHBoxLayout(src_g)
        self.src_list=QListWidget(); self.src_list.setSelectionMode(QListWidget.ExtendedSelection)
        btns=QVBoxLayout()
        self.btn_add_dir=QPushButton("添加目录"); self.btn_add_dir.clicked.connect(self.add_directory)
        self.btn_add_file=QPushButton("添加文件"); self.btn_add_file.clicked.connect(self.add_file)
        self.btn_add_arch=QPushButton("添加压缩包"); self.btn_add_arch.clicked.connect(self.add_archive)
        self.btn_rem=QPushButton("移除所选"); self.btn_rem.clicked.connect(self.remove_sources)
        self.btn_clr=QPushButton("清除历史"); self.btn_clr.clicked.connect(self.clear_history)
        for b in (self.btn_add_dir,self.btn_add_file,self.btn_add_arch,self.btn_rem,self.btn_clr):
            btns.addWidget(b)
        btns.addStretch()
        src_l.addWidget(self.src_list,4); src_l.addLayout(btns,1); ll.addWidget(src_g)

        # CPU核
        cpu_g=QGroupBox("CPU 核心数"); cpu_l=QHBoxLayout(cpu_g)
        cpu_l.addWidget(QLabel("使用核心:")); self.spin_cores=QSpinBox()
        maxc=os.cpu_count() or 1; self.spin_cores.setRange(1,maxc); self.spin_cores.setValue(maxc)
        cpu_l.addWidget(self.spin_cores); cpu_l.addStretch(); ll.addWidget(cpu_g)

        # 参数设置
        params_g=QGroupBox("参数设置"); params_l=QVBoxLayout(params_g)
        # max_results
        l=QHBoxLayout(); l.addWidget(QLabel("最大结果数:"))
        self.spin_max=QSpinBox(); self.spin_max.setRange(1000,100000)
        self.spin_max.setValue(self.config_params["max_results"])
        self.spin_max.valueChanged.connect(lambda v:self.update_param("max_results",v))
        l.addWidget(self.spin_max); l.addStretch(); params_l.addLayout(l)
        # time_range
        l=QHBoxLayout(); l.addWidget(QLabel("每文件小时数:"))
        self.spin_time=QSpinBox(); self.spin_time.setRange(1,24)
        self.spin_time.setValue(self.config_params["time_range_hours"])
        self.spin_time.valueChanged.connect(lambda v:self.update_param("time_range_hours",v))
        l.addWidget(self.spin_time); l.addStretch(); params_l.addLayout(l)
        # chunk_size
        l=QHBoxLayout(); l.addWidget(QLabel("文件分块大小 (KB):"))
        self.spin_chunk=QSpinBox(); self.spin_chunk.setRange(128,8192)
        self.spin_chunk.setValue(self.config_params["chunk_size"]//1024)
        self.spin_chunk.valueChanged.connect(lambda v:self.update_param("chunk_size",v*1024))
        l.addWidget(self.spin_chunk); l.addStretch(); params_l.addLayout(l)
        # thread_timeout
        l=QHBoxLayout(); l.addWidget(QLabel("线程超时 (ms):"))
        self.spin_to=QSpinBox(); self.spin_to.setRange(1000,10000)
        self.spin_to.setValue(self.config_params["thread_timeout"])
        self.spin_to.valueChanged.connect(lambda v:self.update_param("thread_timeout",v))
        l.addWidget(self.spin_to); l.addStretch(); params_l.addLayout(l)
        # max_file_size
        l=QHBoxLayout(); l.addWidget(QLabel("最大文件大小 (MB):"))
        self.spin_fs=QSpinBox(); self.spin_fs.setRange(100,2048)
        self.spin_fs.setValue(self.config_params["max_file_size"]//(1024*1024))
        self.spin_fs.valueChanged.connect(lambda v:self.update_param("max_file_size",v*1024*1024))
        l.addWidget(self.spin_fs); l.addStretch(); params_l.addLayout(l)
        # batch_update_size
        l=QHBoxLayout(); l.addWidget(QLabel("UI 更新批次:"))
        self.spin_bu=QSpinBox(); self.spin_bu.setRange(5,50)
        self.spin_bu.setValue(self.config_params["batch_update_size"])
        self.spin_bu.valueChanged.connect(lambda v:self.update_param("batch_update_size",v))
        l.addWidget(self.spin_bu); l.addStretch(); params_l.addLayout(l)

        params_l.addStretch(); ll.addWidget(params_g); ll.addStretch()

        # 右侧：分组 & 自定义 & 控制 & 调试
        right=QWidget(); rl=QVBoxLayout(right); topSpl.addWidget(right)

        # 分组
        grp_g=QGroupBox("关键词分组（多选）"); grp_l=QVBoxLayout(grp_g)
        self.grp_scroll=QScrollArea(); self.grp_scroll.setWidgetResizable(True)
        cont=QWidget(); self.group_layout=QVBoxLayout(cont); self.grp_scroll.setWidget(cont)
        grp_l.addWidget(self.grp_scroll); rl.addWidget(grp_g)

        # 自定义
        cst_g=QGroupBox("自定义关键词"); cst_l=QHBoxLayout(cst_g)
        self.keyword_combo=QComboBox(editable=True); self.case_box=QCheckBox("区分大小写")
        self.word_box=QCheckBox("全字匹配"); self.regex_box=QCheckBox("使用正则")
        self.btn_add_kw=QPushButton("添加"); self.btn_add_kw.clicked.connect(self.add_custom_keyword)
        self.btn_clear_kw=QPushButton("清除勾选"); self.btn_clear_kw.clicked.connect(self.clear_custom)
        self.btn_sel_all_kw=QPushButton("全选"); self.btn_sel_all_kw.clicked.connect(self.select_all_custom)
        for w in (self.keyword_combo,self.case_box,self.word_box,self.regex_box,
                  self.btn_add_kw,self.btn_clear_kw,self.btn_sel_all_kw):
            cst_l.addWidget(w)
        rl.addWidget(cst_g)
        self.custom_scroll=QScrollArea(); self.custom_scroll.setWidgetResizable(True)
        ccont=QWidget(); self.custom_layout=QVBoxLayout(ccont); self.custom_scroll.setWidget(ccont)
        rl.addWidget(self.custom_scroll)

        # 分析控制
        ana_g=QGroupBox("分析控制"); ana_l=QHBoxLayout(ana_g)
        self.btn_analysis=QPushButton("开始分析"); self.btn_analysis.clicked.connect(self.analyze_combined)
        self.btn_cancel=QPushButton("取消分析"); self.btn_cancel.clicked.connect(self.cancel_analysis)
        self.btn_cancel.setVisible(False)
        self.progress=QProgressBar(); self.progress.setVisible(False)
        ana_l.addWidget(self.btn_analysis); ana_l.addWidget(self.btn_cancel); ana_l.addWidget(self.progress)
        rl.addWidget(ana_g); rl.addStretch()

        # 调试输出
        dbg_g=QGroupBox("调试输出"); dbg_l=QVBoxLayout(dbg_g)
        self.debug=QTextEdit(readOnly=True); dbg_l.addWidget(self.debug)
        mainSpl.addWidget(dbg_g)

        # stretch factors
        topSpl.setStretchFactor(0,1); topSpl.setStretchFactor(1,2)
        mainSpl.setStretchFactor(0,3); mainSpl.setStretchFactor(1,1)

    def update_param(self, key, val):
        self.config_params[key] = val
        self.save_settings()

    def select_config(self):
        cfg,_=QFileDialog.getOpenFileName(self,"选择配置文件","","TOML (*.toml)")
        if cfg:
            try:
                toml.load(cfg)
                self.config_path=cfg
                self.cfg_edit.setText(cfg)
                self.save_settings()
                self.update_group_checkboxes()
            except Exception as e:
                QMessageBox.critical(self,"配置错误",str(e))

    def update_group_checkboxes(self):
        # 清除旧
        for i in reversed(range(self.group_layout.count())):
            w=self.group_layout.itemAt(i).widget()
            if w: w.setParent(None)
        if not self.config_path: return
        cfg=toml.load(self.config_path)
        tot=len(cfg)
        for idx,(gname,gdata) in enumerate(cfg.items()):
            color=generate_color(idx,tot); self.group_colors[gname]=color
            cb=QCheckBox(gname.replace("group.","")); cb.setProperty("group_name",gname)
            self.group_layout.addWidget(cb)

    def add_directory(self):
        d=QFileDialog.getExistingDirectory(self,"选择目录","")
        if d and d not in self.history["sources"]:
            self.history["sources"].insert(0,d)
            self.src_list.insertItem(0,d); self.save_settings()
            worker=DecompressWorker(d, True, self)
            worker.progress.connect(lambda m:self.debug.append(m))
            worker.finished.connect(self.on_decompressed)
            worker.error.connect(lambda e:(self.debug.append(e),QMessageBox.critical(self,"解压错误",e)))
            worker.start()

    def add_file(self):
        f,_=QFileDialog.getOpenFileName(self,"选择文件","","所有文件 (*)")
        if f and f not in self.history["sources"]:
            self.history["sources"].insert(0,f)
            self.src_list.insertItem(0,f); self.save_settings()

    def add_archive(self):
        f,_=QFileDialog.getOpenFileName(self,"选择压缩包","",
            "压缩包 (*.rar *.zip *.7z *.tar *.gz)")
        if f and f not in self.history["sources"]:
            self.history["sources"].insert(0,f)
            self.src_list.insertItem(0,f); self.save_settings()
            worker=DecompressWorker(f, False, self)
            worker.progress.connect(lambda m:self.debug.append(m))
            worker.finished.connect(self.on_decompressed)
            worker.error.connect(lambda e:(self.debug.append(e),QMessageBox.critical(self,"解压错误",e)))
            worker.start()

    def on_decompressed(self, logs: List[str]):
        self.debug.append("解压完成，开始分析")
        self.history["sources"] = logs
        self.src_list.clear()
        for p in logs: self.src_list.addItem(p)
        self.analyze_combined()

    def remove_sources(self):
        for it in self.src_list.selectedItems():
            p=it.text()
            self.history["sources"].remove(p)
            self.src_list.takeItem(self.src_list.row(it))
        self.save_settings()

    def clear_history(self):
        self.history["sources"].clear(); self.src_list.clear(); self.save_settings()

    def get_log_files(self)->List[str]:
        paths=[]; tmpdir=os.path.join(tempfile.gettempdir(),"log_highlighter_decompress")
        for src in self.history["sources"]:
            if os.path.isdir(src):
                for r,_,fs in os.walk(src):
                    for fn in fs:
                        if not fn.lower().endswith(('.rar','.gz')):
                            paths.append(os.path.join(r,fn))
            elif os.path.isfile(src):
                paths.append(src)
        return sorted(set(paths))

    def add_custom_keyword(self):
        txt=self.keyword_combo.currentText().strip()
        if not txt: return
        if txt not in self.history["keywords"]:
            self.history["keywords"].insert(0,txt)
            self.keyword_combo.insertItem(0,txt); self.save_settings()
        parts=[p.strip() for p in txt.split('|') if p.strip()]
        tot=len(self.custom_checks)+len(parts)
        for p in parts:
            idx=len(self.custom_checks); color=generate_color(idx,tot)
            kw=Keyword(p,"[自定义]",self.case_box.isChecked(),
                       self.word_box.isChecked(),self.regex_box.isChecked(),color)
            cb=QCheckBox(p); cb.setProperty("keyword_obj",kw)
            self.custom_checks.append(cb); self.custom_layout.addWidget(cb)

    def clear_custom(self):
        for i in reversed(range(len(self.custom_checks))):
            cb=self.custom_checks[i]
            if cb.isChecked():
                self.custom_layout.removeWidget(cb)
                cb.setParent(None)
                self.custom_checks.pop(i)

    def select_all_custom(self):
        for cb in self.custom_checks: cb.setChecked(True)

    def analyze_combined(self):
        """增量分析：启动/取消关键词扫描"""
        files=self.get_log_files()
        if not files:
            QMessageBox.warning(self,"提示","无日志文件可分析"); return

        # 收集当前关键词 raw
        custom=[cb.property("keyword_obj").raw for cb in self.custom_checks if cb.isChecked()]
        group=[]
        cfg=toml.load(self.config_path) if self.config_path else {}
        for i in range(self.group_layout.count()):
            cb=self.group_layout.itemAt(i).widget()
            if isinstance(cb,QCheckBox) and cb.isChecked():
                grp=cfg.get(cb.property("group_name"),{})
                for v in grp.values():
                    if isinstance(v,dict) and "key" in v:
                        group.append(v["key"])
        current = set(custom+group)
        to_add = current - self.previous_keywords
        to_rem = self.previous_keywords - current

        # 停止并移除已取消关键词线程与结果
        for kw in to_rem:
            if kw in self.current_threads:
                self.current_threads[kw].stop()
            self.cached_results.pop(kw,None)
        self.previous_keywords = current

        # 启动新增关键词扫描线程
        for kw in to_add:
            thread=KeywordScanThread(kw, files, self)
            thread.result_ready.connect(self.on_scan_result)
            self.current_threads[kw]=thread
            thread.start()

    def on_scan_result(self, keyword: str, results: dict):
        # 缓存并更新 HTML
        self.cached_results[keyword]=results
        if keyword in self.current_threads:
            self.current_threads[keyword].quit()
            del self.current_threads[keyword]
        self.update_html_output()

    def cancel_analysis(self):
        for th in self.current_threads.values(): th.stop()
        self.current_threads.clear()
        self.debug.append("已取消分析")

    def update_html_output(self):
        # 合并所有 cached_results 中 current keywords 的匹配，生成 HTML
        files=self.get_log_files()
        out_dir=CONFIG_DEFAULTS["output_dir"]
        for s in self.history["sources"]:
            if os.path.isdir(s): out_dir=s; break
        out_path=os.path.join(out_dir,CONFIG_DEFAULTS["output_filename"]+".html")

        html_lines=["<html><meta charset='utf-8'><body style='font-family:{}'>".format(
            CONFIG_DEFAULTS["html_style"]["font_family"]),
            CONFIG_DEFAULTS["html_style"]["header"]]
        for fp in files:
            file_results=[]
            for kw, res in self.cached_results.items():
                for ln,info in res.get(fp,{}).items():
                    file_results.append((ln,info["content"],info["matches"]))
            if not file_results: continue
            # sort by line number
            html_lines.append(f"<h3>{os.path.basename(fp)}</h3><pre>")
            for ln,content,matches in sorted(file_results,key=lambda x:x[0]):
                # merge overlaps
                matches=sorted(matches,key=lambda x:x[0])
                merged=[]
                for s,e in matches:
                    if not merged or s>merged[-1][1]:
                        merged.append([s,e])
                    else:
                        merged[-1][1]=max(merged[-1][1],e)
                # build highlighted line
                out=""
                last=0
                for s,e in merged:
                    out+=html.escape(content[last:s])
                    out+=f"<span style='background:yellow'>{html.escape(content[s:e])}</span>"
                    last=e
                out+=html.escape(content[last:])
                html_lines.append(f"<span style='color:gray'>{ln:>6}:</span> {out}")
            html_lines.append("</pre>")
        html_lines.append("</body></html>")

        try:
            with open(out_path,'w',encoding='utf-8') as f: f.write("\n".join(html_lines))
            webbrowser.open(f"file://{os.path.abspath(out_path)}")
        except Exception as e:
            logging.error(f"写入 HTML 失败: {e}")

    def load_settings(self):
        if not os.path.exists(self.settings_path): return
        try:
            data=json.load(open(self.settings_path,'r',encoding='utf-8'))
            self.config_path=data.get("config_path")
            if self.config_path: self.cfg_edit.setText(self.config_path)
            h=data.get("history",{})
            self.history["sources"]=h.get("sources",[])
            self.history["keywords"]=h.get("keywords",[])
            self.spin_cores.setValue(h.get("cores",self.spin_cores.value()))
            # 恢复 UI 列表
            for p in self.history["sources"]: self.src_list.addItem(p)
            for kw in self.history["keywords"]: self.keyword_combo.addItem(kw)
            self.update_group_checkboxes()
        except Exception as e:
            logging.error(f"加载设置失败: {e}")

    def save_settings(self):
        self.history["cores"]=self.spin_cores.value()
        self.history["keywords"]=[self.keyword_combo.itemText(i)
                                  for i in range(self.keyword_combo.count())]
        data={"config_path":self.config_path,"history":self.history}
        try:
            if os.path.exists(self.settings_path):
                shutil.copyfile(self.settings_path,self.settings_path+".bak")
            json.dump(data,open(self.settings_path,'w',encoding='utf-8'),ensure_ascii=False)
        except Exception as e:
            logging.error(f"保存设置失败: {e}")

    def closeEvent(self, event):
        self.cancel_analysis()
        QCoreApplication.quit()
        gc.collect()
        event.accept()

if __name__=="__main__":
    app=QApplication(sys.argv)
    win=LogHighlighter()
    win.show()
    sys.exit(app.exec_())