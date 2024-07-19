import sys
import pandas as pd
import numpy as np
import configparser
from PyQt5.QtWidgets import QApplication, QWidget, QVBoxLayout, QPushButton, QFileDialog, QLabel, QTextEdit

def read_spectrum_from_excel(file_path):
    try:
        print(f"Reading spectrum from file: {file_path}")
        df = pd.read_excel(file_path)
        
        # 获取第一列为波长
        wavelengths = df.iloc[:, 0]
        
        # 获取第二列为光谱数据（科学计数法）
        intensities = df.iloc[:, 1]
        
        print(f"Successfully read wavelengths and intensities from {file_path}")
        return wavelengths, intensities
    except Exception as e:
        print(f"Error reading spectrum from Excel file: {e}")
        raise

def spectrum_to_xyz(wavelengths, intensities, cie_xyz_path):
    try:
        print(f"Reading CIE 1931 data from: {cie_xyz_path}")
        cie_xyz = pd.read_csv(cie_xyz_path)
        
        # 只选择波长在360-830nm之间的数据
        valid_indices = (wavelengths >= 360) & (wavelengths <= 830)
        wavelengths = wavelengths[valid_indices]
        intensities = intensities[valid_indices]
        
        # 插值CIE 1931色度匹配函数到所需波长范围
        cie_xyz_interp = cie_xyz.set_index('wavelength').reindex(wavelengths).interpolate(method='linear').reset_index()

        print(f"Converting spectrum to XYZ using wavelengths and intensities")
        x = np.trapz(intensities * cie_xyz_interp['x'].values, wavelengths)
        y = np.trapz(intensities * cie_xyz_interp['y'].values, wavelengths)
        z = np.trapz(intensities * cie_xyz_interp['z'].values, wavelengths)
        
        return np.array([x, y, z])
    except Exception as e:
        print(f"Error in spectrum to XYZ conversion: {e}")
        raise

def xyz_to_rgb(xyz):
    try:
        print(f"Converting XYZ to RGB")
        # XYZ 转 RGB
        matrix = np.array([
            [3.2406, -1.5372, -0.4986],
            [-0.9689, 1.8758, 0.0415],
            [0.0557, -0.2040, 1.0570]
        ])
        
        rgb = np.dot(matrix, xyz)
        
        # Gamma 校正，Gamma = 2.2
        rgb = np.where(rgb <= 0.0031308, 12.92 * rgb, 1.055 * (rgb ** (1 / 2.2)) - 0.055)
        
        # 限制范围在 0 到 1 之间
        rgb = np.clip(rgb, 0, 1)
        
        return rgb
    except Exception as e:
        print(f"Error in XYZ to RGB conversion: {e}")
        raise

class SpectrumAnalyzer(QWidget):
    def __init__(self):
        super().__init__()

        self.initUI()
    
    def initUI(self):
        layout = QVBoxLayout()
        
        self.selectHighTempBtn = QPushButton('选择高色温光谱文件', self)
        self.selectHighTempBtn.clicked.connect(self.openHighTempFile)
        layout.addWidget(self.selectHighTempBtn)
        
        self.selectLowTempBtn = QPushButton('选择低色温光谱文件', self)
        self.selectLowTempBtn.clicked.connect(self.openLowTempFile)
        layout.addWidget(self.selectLowTempBtn)
        
        self.analyzeBtn = QPushButton('开始分析', self)
        self.analyzeBtn.clicked.connect(self.analyzeSpectra)
        layout.addWidget(self.analyzeBtn)
        
        self.resultLabel = QLabel('结果:', self)
        layout.addWidget(self.resultLabel)
        
        self.resultText = QTextEdit(self)
        self.resultText.setReadOnly(True)
        layout.addWidget(self.resultText)
        
        self.setLayout(layout)
        self.setWindowTitle('光谱分析器')
        self.setGeometry(300, 300, 400, 300)
    
    def openHighTempFile(self):
        options = QFileDialog.Options()
        filePath, _ = QFileDialog.getOpenFileName(self, '选择高色温光谱文件', '', 'Excel Files (*.xlsx);;All Files (*)', options=options)
        if filePath:
            self.highTempFilePath = filePath
            self.selectHighTempBtn.setText(f'高色温文件: {filePath}')
    
    def openLowTempFile(self):
        options = QFileDialog.Options()
        filePath, _ = QFileDialog.getOpenFileName(self, '选择低色温光谱文件', '', 'Excel Files (*.xlsx);;All Files (*)', options=options)
        if filePath:
            self.lowTempFilePath = filePath
            self.selectLowTempBtn.setText(f'低色温文件: {filePath}')
    
    def analyzeSpectra(self):
        try:
            config = configparser.ConfigParser()
            config.read('config.ini')
            cie_xyz_path = config['Paths']['cie1931_path']
            print(f"Using CIE 1931 path: {cie_xyz_path}")
            
            high_temp_wavelengths, high_temp_intensities = read_spectrum_from_excel(self.highTempFilePath)
            low_temp_wavelengths, low_temp_intensities = read_spectrum_from_excel(self.lowTempFilePath)
            
            high_temp_xyz = spectrum_to_xyz(high_temp_wavelengths, high_temp_intensities, cie_xyz_path)
            low_temp_xyz = spectrum_to_xyz(low_temp_wavelengths, low_temp_intensities, cie_xyz_path)
            
            high_temp_rgb = xyz_to_rgb(high_temp_xyz)
            low_temp_rgb = xyz_to_rgb(low_temp_xyz)
            
            adjustment_coefficients = low_temp_rgb / high_temp_rgb
            adjustment_coefficients = np.clip(adjustment_coefficients, 0, 1)
            
            result_text = f"RGB调整系数：\nR: {adjustment_coefficients[0]:.4f}\nG: {adjustment_coefficients[1]:.4f}\nB: {adjustment_coefficients[2]:.4f}"
            self.resultText.setText(result_text)
        except Exception as e:
            self.resultText.setText(f"分析过程中出错: {e}")
            print(f"分析过程中出错: {e}")

if __name__ == '__main__':
    app = QApplication(sys.argv)
    ex = SpectrumAnalyzer()
    ex.show()
    sys.exit(app.exec_())