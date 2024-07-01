import sys
import pandas as pd
from PyQt5.QtWidgets import QApplication, QWidget, QVBoxLayout, QPushButton, QFileDialog, QLabel

class BlueLightCalculator(QWidget):
    def __init__(self):
        super().__init__()
        self.initUI()

    def initUI(self):
        layout = QVBoxLayout()

        self.label = QLabel('请选择一个光谱数据文件', self)
        layout.addWidget(self.label)

        self.btn = QPushButton('选择文件', self)
        self.btn.clicked.connect(self.showFileDialog)
        layout.addWidget(self.btn)

        self.result_label = QLabel('', self)
        layout.addWidget(self.result_label)

        self.setLayout(layout)
        self.setWindowTitle('蓝光占比计算器')
        self.show()

    def showFileDialog(self):
        options = QFileDialog.Options()
        file_path, _ = QFileDialog.getOpenFileName(self, '选择光谱数据文件', '', 'Excel Files (*.xlsx *.xls)', options=options)
        if file_path:
            results = self.calculate_blue_light_ratios(file_path)
            if results is not None:
                self.save_results_to_excel(file_path, results)
                self.result_label.setText('蓝光占比计算完成并保存到文件。')
            else:
                self.result_label.setText('无法计算蓝光占比，请检查文件格式和内容。')
        else:
            self.result_label.setText('未选择文件。')

    def calculate_blue_light_ratios(self, file_path):
        try:
            # 读取Excel中的光谱数据，第一行作为列名
            spectrum_data = pd.read_excel(file_path, sheet_name='Sheet1', header=0)

            # 获取波长信息
            wavelengths = spectrum_data.iloc[:, 0]
            results = {}

            # 遍历每一列的光谱数据，从第二列开始
            for column in spectrum_data.columns[1:]:
                # 过滤出蓝光波段（400-500 nm）
                blue_light_data = spectrum_data[(wavelengths >= 400) & (wavelengths <= 500)]

                # 计算蓝光波段的光强度总和
                blue_light_intensity = blue_light_data[column].sum()

                # 计算所有波段的光强度总和
                total_intensity = spectrum_data[column].sum()

                # 计算蓝光占比
                blue_light_ratio = blue_light_intensity / total_intensity
                results[column] = blue_light_ratio

            return results

        except Exception as e:
            print(f"错误: {e}")
            return None

    def save_results_to_excel(self, file_path, results):
        # 创建一个新的DataFrame来保存结果
        results_df = pd.DataFrame(list(results.items()), columns=['Column', 'Blue Light Ratio'])
        results_df['Blue Light Ratio'] = results_df['Blue Light Ratio'].apply(lambda x: f'{x:.2%}')

        # 将结果写入Excel文件中的新工作表
        with pd.ExcelWriter(file_path, engine='openpyxl', mode='a') as writer:
            results_df.to_excel(writer, sheet_name='Blue Light Ratios', index=False)

if __name__ == '__main__':
    app = QApplication(sys.argv)
    ex = BlueLightCalculator()
    sys.exit(app.exec_())