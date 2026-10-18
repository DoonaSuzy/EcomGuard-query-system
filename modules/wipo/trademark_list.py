"""
商标列表管理模块

提供商标数据的加载、保存、查询和筛选功能。
支持从 JSON、CSV、Excel（.xlsx/.xls）文件中读取商标数据，
并支持将数据保存为 JSON 格式。

数据格式说明：
    每条商标记录包含以下标准化字段：
    - brandName (str): 商标名称
    - applicant (str): 申请人名称
    - applicantNames (List[str]): 申请人名称列表（支持多个申请人）
    - registrationNumber (str): 注册号
    - niceClass (List): 尼斯分类
    - notes (str): 备注信息
    - status (str): 商标状态

支持的文件格式：
    - JSON: 支持两种结构：直接为记录列表，或包含 'trademarks' 键的对象
    - CSV: 首行为表头，后续行为数据
    - Excel (.xlsx/.xls): 首行为表头，后续行为数据（依赖 openpyxl 库）
"""

import json
import csv
import os
from typing import List, Dict, Callable, Optional


class TrademarkList:
    """
    商标列表管理类

    提供商标数据的加载、保存、查询和筛选功能。
    内部使用列表存储标准化的商标记录字典。

    Attributes:
        trademarks (List[Dict]): 存储的商标记录列表，每条记录为标准化后的字典
    """

    def __init__(self):
        """
        初始化商标列表

        创建一个空的商标列表实例。
        """
        self.trademarks: List[Dict] = []

    def load_from_json(self, filepath: str) -> int:
        """
        从 JSON 文件加载商标数据

        支持两种 JSON 结构：
        1. 直接为记录列表: [ {...}, {...} ]
        2. 包含 trademarks 键的对象: { "trademarks": [ {...}, {...} ] }

        Args:
            filepath (str): JSON 文件的路径

        Returns:
            int: 加载成功的商标记录数量

        Raises:
            ValueError: 当 JSON 格式不符合上述两种结构时抛出
        """
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, list):
            self.trademarks = [self._normalize_record(item) for item in data]
        elif isinstance(data, dict) and 'trademarks' in data:
            self.trademarks = [self._normalize_record(item) for item in data['trademarks']]
        else:
            raise ValueError("JSON 格式不正确")
        return len(self.trademarks)

    def load_from_csv(self, filepath: str) -> int:
        """
        从 CSV 文件加载商标数据

        CSV 文件首行应为表头，后续行对应各条商标记录。
        表头字段名应与标准化字段名一致（如 brandName, applicant 等）。

        Args:
            filepath (str): CSV 文件的路径

        Returns:
            int: 加载成功的商标记录数量
        """
        with open(filepath, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            self.trademarks = [self._normalize_record(row) for row in reader]
        return len(self.trademarks)

    def load_from_excel(self, filepath: str) -> int:
        """
        从 Excel 文件加载商标数据

        读取第一个工作表（active sheet），首行为表头，
        后续行按表头映射关系构建字典记录。
        需要安装 openpyxl 库以支持 Excel 文件读取。

        Args:
            filepath (str): Excel 文件的路径（.xlsx 或 .xls 格式）

        Returns:
            int: 加载成功的商标记录数量

        Raises:
            ImportError: 当 openpyxl 库未安装时抛出
        """
        try:
            from openpyxl import load_workbook
        except ImportError:
            raise ImportError("需要安装 openpyxl 库: pip install openpyxl")

        wb = load_workbook(filepath, read_only=True)
        ws = wb.active
        # 读取第一行作为表头，用于后续行的字段映射
        headers = [cell.value for cell in next(ws.iter_rows(min_row=1, max_row=1))]
        records = []
        for row in ws.iter_rows(min_row=2):
            record = {}
            for i, cell in enumerate(row):
                # 仅当列索引在表头范围内且表头非空时才映射字段
                if i < len(headers) and headers[i]:
                    record[headers[i]] = cell.value
            records.append(self._normalize_record(record))
        wb.close()
        self.trademarks = records
        return len(self.trademarks)

    def load(self, filepath: str) -> int:
        """
        根据文件扩展名自动选择加载方式

        支持的扩展名：
        - .json: 调用 load_from_json
        - .csv: 调用 load_from_csv
        - .xlsx / .xls: 调用 load_from_excel

        Args:
            filepath (str): 文件路径，将根据扩展名自动识别格式

        Returns:
            int: 加载成功的商标记录数量

        Raises:
            ValueError: 当文件扩展名不在支持列表中时抛出
        """
        ext = os.path.splitext(filepath)[1].lower()
        if ext == '.json':
            return self.load_from_json(filepath)
        elif ext == '.csv':
            return self.load_from_csv(filepath)
        elif ext in ('.xlsx', '.xls'):
            return self.load_from_excel(filepath)
        else:
            raise ValueError(f"不支持的文件格式: {ext}")

    def save_to_json(self, filepath: str):
        """
        将商标数据保存为 JSON 文件

        输出的 JSON 结构为包含 'trademarks' 键的对象，
        使用 UTF-8 编码，缩进为 2 个空格，保留中文字符。

        Args:
            filepath (str): 输出 JSON 文件的路径
        """
        data = {'trademarks': self.trademarks}
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _normalize_record(self, record: Dict) -> Dict:
        """
        将原始记录标准化为统一的数据格式

        字段标准化规则：
        - id: 转为字符串，默认空字符串（保留 Web 版 UUID 主键，供报告/告警溯源）
        - brandName: 转为字符串，默认空字符串
        - applicant: 转为字符串，默认空字符串
        - applicantNames: 若原始值为列表则保留，否则将 applicant 字段值包装为单元素列表
        - registrationNumber: 转为字符串，默认空字符串
        - niceClass: 若原始值为列表则保留，否则默认空列表
        - notes: 转为字符串，默认空字符串
        - status: 保留原始值，默认空字符串

        Args:
            record (Dict): 原始商标记录字典，字段名应与标准化字段名对应

        Returns:
            Dict: 标准化后的商标记录字典，所有字段均已填充默认值
        """
        return {
            # 保留记录 id：MonitorEngine 靠它把报告/告警指回清单里的那一条。
            # 此前这里把它过滤掉了，导致 CLI/CSV/JSON 载入的记录 trademarkId 恒为空。
            'id': str(record.get('id', '') or ''),
            'brandName': str(record.get('brandName', '')),
            'applicant': str(record.get('applicant', '')),
            'applicantNames': record.get('applicantNames', []) if isinstance(record.get('applicantNames'), list) else [str(record.get('applicant', ''))],
            'registrationNumber': str(record.get('registrationNumber', '')),
            'niceClass': record.get('niceClass', []) if isinstance(record.get('niceClass'), list) else [],
            'notes': str(record.get('notes', '')),
            'status': record.get('status', '')
        }

    def get_brand_names(self) -> List[str]:
        """
        获取所有非空的商标名称列表

        Returns:
            List[str]: 去重前的商标名称列表（保留原始顺序），不包含空字符串
        """
        return [t['brandName'] for t in self.trademarks if t['brandName']]

    def get_applicant_names(self) -> List[str]:
        """
        获取所有去重后的申请人名称列表

        遍历所有记录的 applicantNames 字段，收集非空的申请人名称，
        并去除重复项后返回。

        Returns:
            List[str]: 去重后的申请人名称列表（顺序不确定）
        """
        names = set()
        for t in self.trademarks:
            for name in t.get('applicantNames', []):
                if name:
                    names.add(name)
        return list(names)

    def filter_by(self, predicate: Callable[[Dict], bool]) -> 'TrademarkList':
        """
        根据条件筛选商标记录

        创建一个新的 TrademarkList 实例，
        其中仅包含满足谓词条件的记录。

        Args:
            predicate (Callable[[Dict], bool]): 筛选函数，接收一条商标记录字典，
                返回 True 表示保留该记录，False 表示过滤掉

        Returns:
            TrademarkList: 包含筛选结果的新 TrademarkList 实例
        """
        result = TrademarkList()
        result.trademarks = [t for t in self.trademarks if predicate(t)]
        return result

    def __len__(self):
        """
        获取当前商标列表中的记录数量

        Returns:
            int: 商标记录数量
        """
        return len(self.trademarks)

    def __iter__(self):
        """
        迭代器支持

        允许使用 for 循环直接遍历 TrademarkList 实例，
        每次迭代返回一条商标记录字典。

        Returns:
            迭代器对象，逐条返回商标记录字典
        """
        return iter(self.trademarks)

    def __getitem__(self, index):
        """
        索引访问支持

        允许通过下标运算符 [] 访问指定位置的商标记录。

        Args:
            index (int): 记录的索引位置

        Returns:
            Dict: 指定位置的商标记录字典
        """
        return self.trademarks[index]