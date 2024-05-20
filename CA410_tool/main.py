from tkinter import *
import collectBacklightLevel
import threading

def handleButton():
    t = threading.Thread(target=handleRunButton, name='mainLoop')
    t.start()

def handleRunButton():
    minLevelInNormal = int(minLevelInNormalEntry.get())
    maxLevelInNormal = int(maxLevelInNormalEntry.get())
    minLevelInHBM = int(minLevelInHBMEntry.get())
    maxLevelInHBM = int(maxLevelInHBMEntry.get())

    plane = planeVar.get()
    isQCOM = (plane == 'QCOM')
    collectBacklightLevel.runButton(minLevelInNormal, maxLevelInNormal, minLevelInHBM, maxLevelInHBM, isQCOM, 'Driver')

root = Tk()

minLevelInNormal = IntVar()
minLevelInNormalLabel = Label(root, text="手动亮度最小阶数")
minLevelInNormalLabel.grid(row=0, column=0)
minLevelInNormalEntry = Entry(root, bd=5)
minLevelInNormalEntry.insert(0, '1')
minLevelInNormalEntry.grid(row=0, column=1)

maxLightLevelInNormal = IntVar()
maxLevelInNormalLabel = Label(root, text="手动亮度最大阶数")
maxLevelInNormalLabel.grid(row=1, column=0)
maxLevelInNormalEntry = Entry(root, bd=5, textvariable=maxLightLevelInNormal)
maxLevelInNormalEntry.insert(0, '256')
maxLevelInNormalEntry.grid(row=1, column=1)

minLevelInHBM = IntVar()
minLevelInHBMLabel = Label(root, text="HBM最小等级")
minLevelInHBMLabel.grid(row=2, column=0)
minLevelInHBMEntry = Entry(root, bd=5, textvariable=minLevelInHBM)
minLevelInHBMEntry.insert(0, '1')
minLevelInHBMEntry.grid(row=2, column=1)

maxLevelInHBM = IntVar()
maxLevelInHBMLabel = Label(root, text="HBM最大等级")
maxLevelInHBMLabel.grid(row=3, column=0)
maxLevelInHBMEntry = Entry(root, bd=5, textvariable=maxLevelInHBM)  # 修复此行
maxLevelInHBMEntry.insert(0, '4095')
maxLevelInHBMEntry.grid(row=3, column=1)

planeVar = StringVar()
QCOMRadio = Radiobutton(root, text='高通', variable=planeVar, value='QCOM')
QCOMRadio.grid(row=4, column=0)
MTKRadio = Radiobutton(root, text='MTK', variable=planeVar, value='MTK')
MTKRadio.grid(row=4, column=1)

runButton = Button(root, text='run', command=handleButton)
runButton.grid(row=5, column=0)

root.mainloop()
