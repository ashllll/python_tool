import pandas as pd
from PIL import Image, ImageCms
import os
import sys
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor
import configparser
import shutil

def main():
    try:
        # 读取配置文件
        config = configparser.ConfigParser()
        config.read('config.ini')

        # 从配置文件中获取路径
        file_path = config['Paths']['file_path']
        srgb_high_precision_icc = config['Paths']['srgb_high_precision_icc']
        srgb_display_optimized_icc = config['Paths']['srgb_display_optimized_icc']
        display_p3_icc = config['Paths']['display_p3_icc']
        output_base_folder = config['Paths']['output_base_folder']

        # 选择色域
        choice = None
        while choice not in {"1", "2", "3"}:
            print("请选择色域:")
            print("1: 标准sRGB色域（高精度）")
            print("2: 标准sRGB色域（显示优化）")
            print("3: 标准P3色域")
            choice = input("输入1、2或3: ")
            if choice not in {"1", "2", "3"}:
                print("无效的选择，请重新输入。")

        if choice == "1":
            icc_profile_path = srgb_high_precision_icc  # 从配置文件读取路径
            folder_name = "srgb_high_precision"
        elif choice == "2":
            icc_profile_path = srgb_display_optimized_icc  # 从配置文件读取路径
            folder_name = "srgb_display_optimized"
        elif choice == "3":
            icc_profile_path = display_p3_icc  # 从配置文件读取路径
            folder_name = "p3"

        # 检查 ICC 配置文件是否存在
        if not os.path.isfile(icc_profile_path):
            print(f"ICC 配置文件不存在: {icc_profile_path}")
            sys.exit(1)

        # 创建保存图片的文件夹
        output_folder = os.path.join(output_base_folder, folder_name)
        if os.path.exists(output_folder):
            shutil.rmtree(output_folder)
        os.makedirs(output_folder, exist_ok=True)

        # 读取Excel文件
        data = pd.read_excel(file_path)

        # 提取RGB值的列
        rgb_data = data[['R', 'G', 'B']]

        # 将RGB值转换为整数
        rgb_data = rgb_data.astype(int)

        # 获取系统CPU核心数
        max_workers = os.cpu_count()

        # 创建RGB图片的函数
        def create_rgb_image(rgb_values, width=3000, height=3000):
            # 创建一个新的RGB图片
            image = Image.new("RGB", (width, height), rgb_values)
            return image

        # 生成并保存每个RGB值对应的图片的函数
        def process_image(index, row):
            rgb_values = (row['R'], row['G'], row['B'])
            img = create_rgb_image(rgb_values)

            # 应用色彩配置文件
            img = ImageCms.profileToProfile(img, icc_profile_path, icc_profile_path)

            file_path = os.path.join(output_folder, f"rgb_image_{index + 1}.png")
            img.save(file_path)

        # 使用ThreadPoolExecutor进行并行处理
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            list(tqdm(executor.map(lambda args: process_image(*args), rgb_data.iterrows()), total=rgb_data.shape[0], desc="Generating images"))

        print("所有图片已生成。")
    except Exception as e:
        print(f"发生错误: {str(e)}")
    finally:
        sys.exit(0)

if __name__ == "__main__":
    main()
