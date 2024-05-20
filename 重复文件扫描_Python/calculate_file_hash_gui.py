import os
import hashlib
import shutil
from tqdm import tqdm
import argparse
import asyncio
from smbprotocol.connection import Connection
from smbprotocol.session import Session
from smbprotocol.tree import TreeConnect
from smbprotocol.open import Open, CreateDisposition, FileAttributes
from PyQt5.QtWidgets import QApplication, QFileDialog, QMessageBox, QWidget


async def calculate_file_hash(file_path, hash_algo=hashlib.sha256):
    """异步计算文件的哈希值."""
    hash_func = hash_algo()
    loop = asyncio.get_event_loop()
    with open(file_path, 'rb') as f:
        while chunk := await loop.run_in_executor(None, f.read, 8192):
            hash_func.update(chunk)
    return file_path, hash_func.hexdigest()


async def calculate_smb_file_hash(tree, file_path, hash_algo=hashlib.sha256):
    """异步计算 SMB 文件的哈希值."""
    hash_func = hash_algo()
    smb_file = Open(tree, file_path)
    await smb_file.create_async()
    try:
        while chunk := await smb_file.read_async(8192):
            hash_func.update(chunk)
    finally:
        await smb_file.close_async()
    return file_path, hash_func.hexdigest()


async def list_smb_files(tree, path):
    """异步列出SMB路径下的所有文件."""
    smb_files = []
    dir_handle = Open(tree, path, desired_access=FileAttributes.FILE_LIST_DIRECTORY)
    await dir_handle.create_async()
    try:
        async for file_info in dir_handle.query_directory_async("*"):
            filename = file_info['file_name']
            if filename not in ['.', '..']:
                full_path = os.path.join(path, filename)
                if file_info['file_attributes'] & FileAttributes.FILE_DIRECTORY_FILE:
                    smb_files.extend(await list_smb_files(tree, full_path))
                else:
                    smb_files.append(full_path)
    finally:
        await dir_handle.close_async()
    return smb_files


async def find_duplicate_files(directory, temp_dir, is_smb=False):
    """查找并移动重复文件，显示扫描进度和重复文件名."""
    file_hashes = {}
    duplicates = []

    if not os.path.exists(temp_dir):
        os.makedirs(temp_dir)

    all_files = []

    if is_smb:
        # 从环境变量获取SMB连接信息
        server = os.getenv('SMB_SERVER')
        username = os.getenv('SMB_USERNAME')
        password = os.getenv('SMB_PASSWORD')
        share = os.getenv('SMB_SHARE')

        # 如果环境变量未设置，提示用户输入
        if not server:
            server = input("请输入SMB服务器地址: ").strip()
        if not username:
            username = input("请输入SMB用户名: ").strip()
        if not password:
            password = input("请输入SMB密码: ").strip()
        if not share:
            share = input("请输入SMB共享路径: ").strip()

        connection = Connection(uuid=os.urandom(16), username=username, password=password, server=server)
        connection.connect()
        session = Session(connection)
        session.connect()
        tree = TreeConnect(session, share)
        tree.connect()

        all_files = await list_smb_files(tree, "/")
    else:
        for dirpath, _, filenames in os.walk(directory):
            for filename in filenames:
                file_path = os.path.join(dirpath, filename)
                if os.path.isfile(file_path):
                    all_files.append(file_path)

    # 使用异步任务计算文件哈希值
    tasks = []
    for file_path in all_files:
        if is_smb:
            tasks.append(calculate_smb_file_hash(tree, file_path))
        else:
            tasks.append(calculate_file_hash(file_path))

    # 使用异步信号量控制并发任务的数量
    sem = asyncio.Semaphore(50)  # 调整信号量的值以控制并发数

    async def sem_task(task):
        async with sem:
            return await task

    tasks = [sem_task(task) for task in tasks]

    for future in tqdm(asyncio.as_completed(tasks), total=len(tasks), desc="Scanning files"):
        try:
            file_path, file_hash = await future
            if file_hash in file_hashes:
                duplicates.append((file_path, file_hashes[file_hash]))
            else:
                file_hashes[file_hash] = file_path
        except Exception as exc:
            print(f"{file_path} generated an exception: {exc}")

    # 移动重复文件并显示重复文件名
    for duplicate, original in duplicates:
        print(f"Duplicate file: {duplicate} (original: {original})")
        try:
            shutil.move(duplicate, os.path.join(temp_dir, os.path.basename(duplicate)))
        except Exception as exc:
            print(f"Failed to move {duplicate}: {exc}")

    return duplicates


def select_directory():
    """选择要扫描的目录."""
    app = QApplication([])
    widget = QWidget()
    directory = QFileDialog.getExistingDirectory(widget, '选择要扫描的目录')
    return directory


def main():
    parser = argparse.ArgumentParser(description="扫描并移动重复文件到指定文件夹")
    parser.add_argument("--smb", action="store_true", help="如果路径是 SMB 共享，则添加此标志")
    parser.add_argument("--temp_dir", default="duplicates_temp", help="存储重复文件的临时文件夹")
    args = parser.parse_args()

    if args.smb:
        directory_to_scan = select_directory()
        if not directory_to_scan.startswith("/Volumes/"):
            QMessageBox.critical(None, "错误", f"{directory_to_scan} 不是一个有效的 SMB 路径")
            return
    else:
        directory_to_scan = select_directory()
        if not os.path.isdir(directory_to_scan):
            QMessageBox.critical(None, "错误", f"{directory_to_scan} 不是一个有效的目录路径")
            return

    duplicates = asyncio.run(find_duplicate_files(directory_to_scan, args.temp_dir, is_smb=args.smb))
    if duplicates:
        QMessageBox.information(None, "扫描完成",
                                f"发现并移动了 {len(duplicates)} 个重复文件到 {args.temp_dir} 文件夹。")
        user_choice = QMessageBox.question(None, "删除文件夹", f"是否删除 {args.temp_dir} 文件夹？",
                                           QMessageBox.Yes | QMessageBox.No)
        if user_choice == QMessageBox.Yes:
            shutil.rmtree(args.temp_dir)
            QMessageBox.information(None, "删除完成", f"已删除 {args.temp_dir} 文件夹。")
        else:
            QMessageBox.information(None, "手动删除", f"请手动检查并删除 {args.temp_dir} 文件夹。")
    else:
        QMessageBox.information(None, "扫描完成", "没有发现重复文件。")


if __name__ == "__main__":
    main()
