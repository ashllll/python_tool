import serial
import serial.tools.list_ports
import time
from datetime import datetime

class CA410:
    def __init__(self):
        self.baudrate = 38400
        self.bytesize = serial.SEVENBITS
        self.parity = serial.PARITY_EVEN
        self.stopbits = serial.STOPBITS_TWO
        self.timeout = 1
        self.connection = None
        self.data = []

    def find_ca410_port(self):
        """查找并返回第一个可用的CA-410设备的COM端口"""
        ports = list(serial.tools.list_ports.comports())
        for port in ports:
            try:
                connection = serial.Serial(
                    port=port.device,
                    baudrate=self.baudrate,
                    bytesize=self.bytesize,
                    parity=self.parity,
                    stopbits=self.stopbits,
                    timeout=self.timeout,
                    rtscts=True
                )
                time.sleep(2)  # 等待设备准备就绪
                connection.write("COM,1\r".encode())  # 发送启动通信命令
                time.sleep(1)
                response = connection.read_all().decode().strip()
                connection.close()
                if "OK00" in response:
                    print(f"找到CA-410设备，端口: {port.device}")
                    return port.device
            except (OSError, serial.SerialException):
                print(f"端口 {port.device} 已被占用或无法打开")
        return None

    def connect(self):
        """连接到CA-410设备"""
        port = self.find_ca410_port()
        if port:
            try:
                self.connection = serial.Serial(
                    port=port,
                    baudrate=self.baudrate,
                    bytesize=self.bytesize,
                    parity=self.parity,
                    stopbits=self.stopbits,
                    timeout=self.timeout,
                    rtscts=True
                )
                print(f"CA-410连接成功，端口: {port}")
            except Exception as e:
                print(f"连接CA-410设备时出错: {e}")
        else:
            print("没有找到可用的CA-410设备")

    def disconnect(self):
        """断开与CA-410设备的连接"""
        if self.connection:
            self.connection.close()
            print("CA-410已断开连接")

    def send_command(self, command, wait_time=1):
        """发送命令到设备并读取响应"""
        if self.connection:
            command = command + '\r'
            self.connection.write(command.encode())
            time.sleep(wait_time)
            response = self.connection.read_all().decode().strip()
            return response
        else:
            print("设备未连接")
            return None

    def initialize_device(self):
        """初始化设备"""
        response = self.send_command("COM,1", wait_time=1)
        print(f"初始化响应: {response}")
        self.set_display_mode_xy()  # 在初始化设备时设置一次显示模式

    def set_display_mode_xy(self):
        """设置显示模式为x、y、LV"""
        response = self.send_command("MDS,0", wait_time=1)
        print(f"设置显示模式为x、y、LV: {response}")

    def zero_calibration(self):
        """校零操作"""
        response = self.send_command("ZRC", wait_time=5)
        print(f"校零响应: {response}")

    def measure(self):
        """测量操作"""
        response = self.send_command("MES,2", wait_time=2)
        print(f"测量响应: {response}")
        return response

    def get_measurement_values(self):
        """获取测量数据"""
        xy_response = self.measure()
        if xy_response and xy_response.startswith("OK00"):
            xy_data = xy_response.split(',')
            print(f"x和y响应数据长度: {len(xy_data)}")
            print(f"x和y响应数据: {xy_data}")

            if len(xy_data) >= 11:
                measurement_data = {
                    'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'x': float(xy_data[3]),  # x值
                    'y': float(xy_data[4]),  # y值
                    'Lv': float(xy_data[5])  # Lv值
                }
                return measurement_data
            else:
                print("测量响应格式错误")
                return None
        else:
            print("x和y测量响应格式错误")
            return None

def save_to_excel(data, filename):
    """保存数据到Excel文件"""
    df = pd.DataFrame(data)
    df.to_excel(filename, index=False)
    print(f"测量数据已保存到 {filename}")

def main():
    ca410 = CA410()

    # 连接设备
    ca410.connect()

    # 初始化设备
    ca410.initialize_device()

    # 校零
    ca410.zero_calibration()

    # 获取测量数据
    measurements = []
    measurement = ca410.get_measurement_values()
    if measurement:
        print(f"测量结果: {measurement}")
        measurements.append(measurement)

    # 保存测量数据到Excel
    if measurements:
        save_to_excel(measurements, 'ca410_measurements.xlsx')

    # 断开连接
    ca410.disconnect()

if __name__ == "__main__":
    main()
