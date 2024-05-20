import time, os, csv, clipboard, logging, subprocess
from collections import Counter
import pyautogui

logger = logging.getLogger('logger')
def configLogger():
    logName = time.strftime('%Y%m%d%H%M', time.localtime(time.time())) + '.log'

    handlerStream = logging.StreamHandler()
    handlerFile = logging.FileHandler(filename=logName)
    logger.setLevel(logging.DEBUG)
    handlerStream.setLevel(logging.DEBUG)
    handlerFile.setLevel(logging.DEBUG)

    formatter = logging.Formatter("%(asctime)s - %(filename)s[line:%(lineno)d] - %(levelname)s: %(message)s")
    handlerStream.setFormatter(formatter)
    handlerFile.setFormatter(formatter)

    logger.addHandler(handlerStream)
    logger.addHandler(handlerFile)
configLogger()

class Phone:
    def __init__(self, plane, interval=0.2):
        self.mMTKLightNode = '/sys/class/leds/lcd-backlight/brightness'
        self.mQCOMLightNode = '/sys/class/backlight/panel0-backlight/brightness'
        self.highBrightnessNode = '/sys/devices/platform/soc/ae00000.qcom,mdss_mdp/backlight/panel0-backlight/brightness'
        if plane == 'QCOM':
            self.mLightNode = self.mQCOMLightNode
        elif plane == 'MTK':
            self.mLightNode = self.mMTKLightNode
        self.mDimmingMethod = 'Db'
        self.interval = interval
        logger.debug('self.mLightNode=' + self.mLightNode)
        logger.debug('self.mDimmingMethod=' + self.mDimmingMethod)
        logger.debug('self.interval=' + str(self.interval))

    def inputCmd(self, cmd):
        logger.debug('inputCmd:' + cmd)
        result = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return result.stdout

    def setNormalLightLevel(self, level):
        cmd = f'adb shell settings put system screen_brightness {level}'
        self.inputCmd(cmd)
        time.sleep(self.interval)

    def setHighLightLevel(self, level):
        cmd = f'adb shell "echo {level} > {self.highBrightnessNode}"'
        self.inputCmd(cmd)
        time.sleep(self.interval)

    def inputCmd(self, cmd):
        logger.debug('inputCmd:'+cmd)
        return os.popen(cmd)

    def getPanelState(self):
        return (self.inputCmd('adb shell dumpsys window policy | findstr screen')).read()

    def setLightLevel(self, level):
        if self.mDimmingMethod.find('Driver') != -1:
            self.setLightLevelByDriver(level)
        elif self.mDimmingMethod.find('Db') != -1:
            self.setLightLevelByDb(level)
        else:
            logger.warning('setLightLevel failself.mDimmingMethod is {}'.format(self.mDimmingMethod))

    def getLightLevel(self):
        ret = None
        if self.mDimmingMethod.find('Driver') != -1:
            ret = self.getLightLevelByDriver()
        elif self.mDimmingMethod.find('Db') != -1:
            ret = self.getLightLevelByDb()
        else:
            logger.warning('setLightLevel failself.mDimmingMethod is {}'.format(self.mDimmingMethod))
        logger.info('Phone.getLightLevel->lightLevel={}'.format(ret))
        return ret

    def setLightLevelByDriver(self, level):
        cmd = 'adb shell "echo ' + str(level) + ' > ' + self.mLightNode + '"'
        self.inputCmd(cmd)
        time.sleep(self.interval)

    def getLightLevelByDriver(self):
        cmd = 'adb shell cat ' + self.mLightNode
        return (self.inputCmd(cmd)).read()[:-1]

    def setLightLevelByDb(self, level):
        cmd = 'adb shell settings put system screen_brightness ' + str(level)
        self.inputCmd(cmd)
        time.sleep(self.interval)

    def getLightLevelByDb(self):
        cmd = 'adb shell settings get system screen_brightness'
        return (self.inputCmd(cmd)).read()[:-1]

    def setPhyLightLevel(self, level):
        level_h = '%02x' % int(level / 256)
        level_l = '%02x' % int(level % 256)
        cmd = 'adb shell "echo 51 ' + level_h + ' ' + level_l + ' > /sys/kernel/oppo_display/write_panel_reg"'
        self.inputCmd(cmd)
        time.sleep(self.interval)

    def getPhyLightLevel(self):
        cmd = 'adb shell "echo r 52 2 > /sys/kernel/oppo_display/write_panel_reg"'
        self.inputCmd(cmd)
        cmd = 'adb shell cat /sys/kernel/oppo_display/write_panel_reg'
        value = self.inputCmd(cmd)
        values = value.split()
        return int(values[0], 16) * 256 + int(values[0][1], 16)

    def getLux(self):
        cmd = 'adb shell "logcat -s AIBrightnessModel -t 10000 | grep -e \'handleUpdateBrightness mLux\'"'
        #cmd = 'adb shell "logcat -s DeepThinker -t 10000 | grep -e \'handleUpdateBrightness mLux\'"'
        #cmd = 'adb shell "logcat -s AIBrightnessModel -t 10000 | grep -e \'setLux, lux\'"'
        #cmd = 'adb shell "logcat -s OppoBrightUtils -t 10000 | grep -e \'setAIBrightnessLux lux\'"'
        ret = subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.PIPE,shell=True)
        logger.debug('Phone.getLux->debug:cmd={}'.format(cmd))
        outLines = ret.stdout.readlines()
        logger.debug('Phone.getLux->debug:return={}'.format(outLines))
        luxList = []
        lux = None
        for line in outLines:
            line = line[:-2].decode('UTF-8')
            try:
                lux = float(line.split(':')[-1])
                #lux = float(line.split('=')[-1])
                luxList.append(round(lux))
            except ValueError:
                logger.debug('Phone.getLux->debug log output:{}'.format(luxList))
        try:
            lux = Counter(luxList).most_common(1)[0][0]
        except:
            logger.warning('Phone.getLux->error:{}'.format(luxList))
            lux = -1
        ret.terminate()
        logger.info('Phone.getLux->lux={}'.format(lux))
        return lux

    def cleanLog(self):
        cmd = 'adb shell logcat -c'
        self.inputCmd(cmd)
        time.sleep(self.interval)

    def wakeupALS(self):  # 锁屏并重新亮屏
        state = self.getPanelState()
        logger.debug('getPanelState:'+state)
        if state.find('SCREEN_STATE_OFF') != -1:
            self.inputCmd('adb shell input keyevent 26')
            time.sleep(self.interval + 1)
        self.inputCmd('adb shell input keyevent 26')
        time.sleep(self.interval + 1)
        self.inputCmd('adb shell input keyevent 26')
        time.sleep(self.interval + 1)
        self.inputCmd('adb shell input keyevent 82')
        time.sleep(self.interval + 2)

    def openWhiteImg(self):
        cmd = 'adb shell am start com.oplus.launcher/com.oplus.launcher.Launcher'
        self.inputCmd(cmd)
        time.sleep(1)
        cmd = 'adb shell am start com.oplus.engineermode/com.oplus.engineermode.lcd.modeltest.LCDColorTest'
        self.inputCmd(cmd)
        for item in range(3):
            time.sleep(self.interval)
            cmd = 'adb shell input tap 500 500'
            self.inputCmd(cmd)

    def openBlackImg(self):
        cmd = 'adb shell am start com.oplus.launcher/com.oplus.launcher.Launcher'
        self.inputCmd(cmd)
        time.sleep(0.2)
        cmd = 'adb shell am start com.oplus.engineermode/com.oplus.engineermode.lcd.modeltest.LCDColorTest'
        self.inputCmd(cmd)
        for item in range(4):
            time.sleep(self.interval)
            cmd = 'adb shell input tap 500 500'
            self.inputCmd(cmd)
            time.sleep(1)

    def closeWhiteImg(self):
        for item in range(6):
            self.inputCmd(r'adb shell input tap 500 500')
            time.sleep(self.interval)

    def closeBlackImg(self):
        for item in range(5):
            self.inputCmd(r'adb shell input tap 500 500')
            time.sleep(self.interval)

    def changeAutoBacklight(self, state):
        cmd = 'adb shell settings put system screen_brightness_mode ' + state
        self.inputCmd(cmd)

    def closeAutoBacklight(self):
        self.changeAutoBacklight('0')

    def openAutoBacklight(self):
        self.changeAutoBacklight('1')

    def reboot(self):
        cmd = 'adb shell reboot'
        self.inputCmd(cmd)

    def disablePSensor(self):
        cmd = 'adb shell dumpsys display psensor 0'
        self.inputCmd(cmd)

    def openPanic(self):
        cmd = 'adb shell setprop persist.sys.assert.panic 1'
        self.inputCmd(cmd)
        cmd = 'adb shell dumpsys  display log all 1'
        self.inputCmd(cmd)

# 添加 Labsphere 类
class Labsphere:
    def inputEnterIfWarningShow(self):
        # 模拟输入确认
        pass

    def inputLuxValue(self, lux):
        # 输入亮度值
        pass

    def clickSetLuxValue(self):
        # 点击设置亮度值
        pass


def writeBacklightLevelCsv(rawFile, outFile, backlightLevel):
    with open(rawFile, newline='') as csvRawfile:
        with open(outFile, 'w', newline='') as csvOutFile:
            levelIter = iter(backlightLevel)
            rawReader = csv.reader(csvRawfile, delimiter=' ', quotechar='|')
            outWriter = csv.writer(csvOutFile)
            firstFlag = True
            for line in rawReader:
                if firstFlag is True:
                    outWriter.writerow(['LevelNo', 'Lv'])
                    firstFlag = False
                    continue

                item = line[0].split(',')
                try:
                    outWriter.writerow([next(levelIter), str(round(float(item[6]), 3))])
                except StopIteration:
                    logger.error(r'iter range over')
                    continue


def writeCsvSelectRequiedBacklightLevel(rawFile, outFile, lvList, maxLevelInNormal, maxLevel):
    rawlevelList = []
    rawLvList = []
    with open(rawFile, newline='') as csvRawfile:
        firstFlag = True
        for line in csv.reader(csvRawfile, delimiter=' ', quotechar='|'):
            if firstFlag is True:
                firstFlag = False
                continue
            rawlevelList.append(int(line[0].split(',')[0]))
            rawLvList.append(float(line[0].split(',')[1]))
    selectBestLevel = []
    selectBestDeviation = []
    levelList = []
    for item in lvList:
        for value in rawLvList:
            if item - value >= 3:
                continue
            deviation = abs(item - value)
            if deviation < 3:
                selectBestLevel.append(value)
                selectBestDeviation.append(deviation)
        if len(selectBestDeviation) == 0:
            levelList.append('NULL')
            continue
        bestValue = selectBestLevel[ selectBestDeviation.index( min( selectBestDeviation))]
        level = rawlevelList[ rawLvList.index( bestValue)]
        levelList.append(level)
        selectBestLevel = []
        selectBestDeviation = []
    try:
        lvList.insert(57, rawLvList[rawlevelList.index(maxLevelInNormal)])
        levelList.insert(57, maxLevelInNormal)
    except ValueError:
        pass
    try:
        lvList.append(rawLvList[rawlevelList.index(maxLevel)])
        levelList.append(maxLevel)
    except ValueError:
        pass
    with open(outFile, 'w', newline='') as csvOutFile:
        outWriter = csv.writer(csvOutFile)
        outWriter.writerow(['Lv', 'Level'])
        for iLv, iLevel in zip(lvList, levelList):
            outWriter.writerow([iLv, iLevel])

def writeBacklitCurveAddNit(levelCsv, luxCsv, curveCsv):
    levelLvDir = {}
    with open(levelCsv, newline='') as levelFd:
        levelReader = csv.reader(levelFd)
        firstFlag = True
        for line in levelReader:
            if firstFlag is True:
                firstFlag = False
                continue
            levelLvDir[line[0]] = line[1]
    with open(luxCsv, 'r') as luxFd:
        lines = luxFd.readlines()
    for index, line in enumerate(lines):
        if line.find('Level') != -1:
            temp = 'Lv,'
            for num in line.split(','):
                num = num.replace('\n', '')
                if num in levelLvDir:
                    temp += levelLvDir[num] + ','
            temp = temp[:-1]
            temp += '\n'
            lines.insert(index+1, temp)

    with open(curveCsv, 'w') as curveFd:
        curveFd.write(''.join(lines))

def perform_measurement(save_path):
    # 模拟测量结果
    measurement_result = "测量结果"

    # 将结果保存到指定路径
    result_file = os.path.join(save_path, 'measurement_results.txt')
    with open(result_file, 'w') as file:
        file.write(measurement_result)

if __name__ == '__main__':
    '''
    phone = Phone(True, 'Db', 0.2)
    phone.cleanLog()
    #time.sleep(1)
    value = phone.getLux()
    print(value)'''
    backlightLevel = 4095
    rawFile = r'D:\ColorMeasurement.csv'
    outFile = r'D:\out.csv'
    backlightLevel = range(1, 4095 + 1, 1)
    writeBacklightLevelCsv(rawFile, outFile, backlightLevel)
