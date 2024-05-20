import sys
import os
import time
import csv
from utility import Phone, Labsphere, logger, writeBacklightLevelCsv
from CA410 import CA410

print("Current working directory:", os.getcwd())
print("Python search path:", sys.path)

def collectBacklightLevel(rawFile, outFile, minLevelInNormal, maxLevelInNormal, minLevelInHBM, maxLevelInHBM, plane, interval=0.1):
    phone = Phone(plane)
    phone.wakeupALS()
    phone.closeAutoBacklight()
    phone.openWhiteImg()

    ca410 = CA410()
    ca410.connect()
    ca410.initialize_device()  # 初始化设备时设置显示模式
    ca410.zero_calibration()

    measurements = []

    if minLevelInNormal == maxLevelInNormal == 1:
        # 测量高亮模式
        for level in range(minLevelInHBM, maxLevelInHBM + 1):
            phone.setHighLightLevel(level)
            time.sleep(interval)  # 优化测量时间
            measurement = ca410.get_measurement_values()
            if measurement:
                print(f"测量结果 (高亮): {measurement}")
                measurements.append((level, measurement))
    elif minLevelInHBM == maxLevelInHBM == 1:
        # 测量常规亮度范围
        for level in range(minLevelInNormal, maxLevelInNormal + 1):
            phone.setNormalLightLevel(level)
            time.sleep(interval)  # 优化测量时间
            measurement = ca410.get_measurement_values()
            if measurement:
                print(f"测量结果 (正常): {measurement}")
                measurements.append((level, measurement))
    else:
        # 测量正常亮度范围
        for level in range(minLevelInNormal, maxLevelInNormal + 1):
            phone.setNormalLightLevel(level)
            time.sleep(interval)  # 优化测量时间
            measurement = ca410.get_measurement_values()
            if measurement:
                print(f"测量结果 (正常): {measurement}")
                measurements.append((level, measurement))

        # 测量高亮模式
        for level in range(minLevelInHBM, maxLevelInHBM + 1):
            phone.setHighLightLevel(level)
            time.sleep(interval)  # 优化测量时间
            measurement = ca410.get_measurement_values()
            if measurement:
                print(f"测量结果 (高亮): {measurement}")
                measurements.append((level, measurement))

    ca410.disconnect()
    phone.closeWhiteImg()

    # 保存测量结果到文件
    with open(outFile, 'w', newline='') as csvOutFile:
        csvWriter = csv.writer(csvOutFile)
        csvWriter.writerow(['Backlight Level', 'x', 'y', 'Lv'])
        for level, measurement in measurements:
            csvWriter.writerow([level, measurement['x'], measurement['y'], measurement['Lv']])

    logger.info(f"Backlight level measurements saved to {outFile}")

def runButton(minLevelInNormal, maxLevelInNormal, minLevelInHBM, maxLevelInHBM, isQCOM, dimmingMethod):
    rawFile = r'D:\\ColorMeasurement.csv'
    outFile = r'D:\\outLevel.csv'

    plane = 'QCOM' if isQCOM else 'MTK'
    collectBacklightLevel(rawFile, outFile, minLevelInNormal, maxLevelInNormal, minLevelInHBM, maxLevelInHBM, plane)

if __name__ == '__main__':
    minLevelInNormal = 1
    maxLevelInNormal = 256
    minLevelInHBM = 1
    maxLevelInHBM = 4095
    isQCOM = True  # True for QCOM, False for MTK
    dimmingMethod = 'Driver'  # or 'Db'

    runButton(minLevelInNormal, maxLevelInNormal, minLevelInHBM, maxLevelInHBM, isQCOM, dimmingMethod)
