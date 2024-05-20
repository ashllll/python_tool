import time
import csv
from utility import Phone, Labsphere, logger, writeBacklightLevelCsv  # 确保从 utility 导入所需函数
from ca410 import CA410  # 确保 CA410 类所在的文件名为 CA410.py

def func1(phone):
    phone.openAutoBacklight()
    yield
    time.sleep(6)

def func2(phone):
    phone.closeAutoBacklight()
    yield
    phone.openAutoBacklight()
    time.sleep(6)

def func3(phone):
    phone.closeAutoBacklight()
    phone.setLightLevel(0)
    yield
    phone.openAutoBacklight()
    time.sleep(8)

def func4(phone):
    phone.closeAutoBacklight()
    phone.setLightLevel(2047)
    yield
    phone.openAutoBacklight()
    time.sleep(8)

def loopForCollectLux(luxList, labsphere, phone, func, oLuxList, oBacklightLevelList, oRealLuxList):
    for lux in luxList:
        gFun = func(phone)
        next(gFun)
        labsphere.inputEnterIfWarningShow()
        labsphere.inputLuxValue(lux)
        labsphere.clickSetLuxValue()
        labsphere.inputEnterIfWarningShow()
        phone.cleanLog()
        try:
            next(gFun)
        except StopIteration:
            pass
        realLux = phone.getLux()
        level = phone.getLightLevel()
        oLuxList.append(lux)
        oRealLuxList.append(realLux)
        oBacklightLevelList.append(level)

def collectLuxToLevel(luxList, outLuxFile, isQCOM, dimmingMethod, maxLightLevelInNormal, funcList=[func1, func2, func3, func4]):
    plane = 'QCOM' if isQCOM else 'MTK'
    phone = Phone(plane, dimmingMethod, maxLightLevelInNormal)
    phone.openPanic()
    phone.reboot()
    time.sleep(60)

    phone.wakeupALS()
    phone.closeAutoBacklight()
    phone.openBlackImg()
    phone.disablePSensor()
    phone.openPanic()

    labsphere = Labsphere()
    oLuxList = ['Lux']
    oRealLuxList = ['RealLux']
    oBacklightLevelList = ['Level']

    with open(outLuxFile, 'w', newline='') as csvOutFile:
        outWriter = csv.writer(csvOutFile)

        for item in funcList:
            outWriter.writerow([item.__name__])
            loopForCollectLux(luxList, labsphere, phone, item, oLuxList, oBacklightLevelList, oRealLuxList)
            loopForCollectLux(list(reversed(luxList)), labsphere, phone, item, oLuxList, oBacklightLevelList, oRealLuxList)
            outWriter.writerow(oLuxList)
            outWriter.writerow(oRealLuxList)
            outWriter.writerow(oBacklightLevelList)
            oLuxList = ['Lux']
            oRealLuxList = ['RealLux']
            oBacklightLevelList = ['Level']

    phone.closeBlackImg()

if __name__ == '__main__':
    time.sleep(5)
    luxList = [
        8, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70,
        75, 80, 85, 90, 95, 100, 120, 140, 160, 180, 200, 220, 240, 260, 280,
        300, 320, 340, 380, 400, 420, 440, 460, 480, 500, 700, 900, 1100, 1300,
        1500, 1700, 1900, 2000, 2500, 3000, 3500, 4000, 4500, 5000
    ]
    luxList = [8, 16, 30, 60, 130, 260, 540, 745, 1025, 1480, 2060, 2940, 4220, 5900, 8300, 32000]
    luxList = [10, 30, 50, 70, 100, 200, 300, 500, 1100, 1700, 2500, 4000, 5000, 7000]
    outLuxFile = r'D:\outLux.csv'
    maxLightLevelInNormal = 1023
    collectLuxToLevel(luxList, outLuxFile, True, 'Driver', maxLightLevelInNormal, [func1, func2, func3, func4])

    input("success end")
