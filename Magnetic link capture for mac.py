from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup


def fetch_magnet_links_with_selenium(url):
    # 设置Safari WebDriver
    driver = webdriver.Safari()  # 不需要executable_path参数
    try:
        driver.get(url)

        # 使用WebDriverWait等待页面某元素加载完成
        wait = WebDriverWait(driver, 10)  # 等待最长10秒
        wait.until(EC.presence_of_element_located((By.TAG_NAME, 'body')))  # 以<body>标签的加载作为页面加载完成的标志

        # 使用Selenium获取页面源代码
        page_source = driver.page_source

    finally:
        driver.quit()

    # 使用BeautifulSoup解析页面源代码
    soup = BeautifulSoup(page_source, 'html.parser')
    magnet_links = [link.get('href') for link in soup.find_all('a') if link.get('href', '').startswith('magnet:')]

    # 将磁力链接写入文件
    if magnet_links:
        with open('magnet_links.txt', 'w', encoding='utf-8') as file:
            for magnet_link in magnet_links:
                file.write(f"{magnet_link}\n")
        print(f"磁力链接已保存到 magnet_links.txt，共{len(magnet_links)}个链接。")
    else:
        print("未找到磁力链接。")


if __name__ == "__main__":
    url_input = input("请输入要抓取的网页URL: ")
    fetch_magnet_links_with_selenium(url_input)
