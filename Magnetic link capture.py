from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup  # 确保导入BeautifulSoup
import time

def fetch_magnet_links_with_selenium(url):
    # 设置Selenium WebDriver
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()))
    driver.get(url)
    time.sleep(5)  # 等待页面加载完成，这里的等待时间可能需要根据实际页面加载速度进行调整

    # 使用Selenium的页面源代码
    page_source = driver.page_source
    driver.quit()

    # 使用BeautifulSoup解析页面源代码
    soup = BeautifulSoup(page_source, 'html.parser')
    magnet_links = [link.get('href') for link in soup.find_all('a') if link.get('href', '').startswith('magnet:')]

    # 写入文件操作与前相同
    if magnet_links:
        with open('magnet_links.txt', 'w') as file:
            for magnet_link in magnet_links:
                file.write(f"{magnet_link}\n")
        print(f"磁力链接已保存到 magnet_links.txt，共{len(magnet_links)}个链接。")
    else:
        print("未找到磁力链接。")

if __name__ == "__main__":
    url_input = input("请输入要抓取的网页URL: ")
    fetch_magnet_links_with_selenium(url_input)

######
