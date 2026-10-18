"""
CryptoJS 兼容的 AES 加解密模块

功能说明:
    本模块实现了与 JavaScript CryptoJS 库完全兼容的 AES 加解密算法。
    主要用于解密 WIPO BrandDB API 返回的加密响应数据。

技术背景:
    WIPO API 使用 CryptoJS 进行 AES-ECB 加密，密钥长度为 48 字节（384位）。
    标准 AES 实现不支持 48 字节密钥，因此需要自定义实现以完全兼容 CryptoJS 的行为。

实现特点:
    - 支持非标准密钥长度（如 48 字节）
    - 使用查找表优化性能（SUB_MIX 和 INV_SUB_MIX 表）
    - 实现 CryptoJS 兼容的密钥扩展算法
    - 支持 PKCS7 填充/去填充

算法流程:
    加密: 明文 -> PKCS7填充 -> AES-ECB加密 -> 密文
    解密: 密文 -> AES-ECB解密 -> PKCS7去填充 -> 明文

使用方式:
    aes = CryptoJSAES(key)
    encrypted = aes.encrypt(plaintext)
    decrypted = aes.decrypt(ciphertext)

作者: 商标监控系统开发团队
版本: 1.0.0
"""

import struct
import base64
import os
from typing import Optional, Tuple


# ==================== AES S-Box ====================
# AES 标准 S-Box (替换表)，用于字节替换操作
# 这是 AES 算法的核心组件之一
SBOX = bytes([
    0x63, 0x7c, 0x77, 0x7b, 0xf2, 0x6b, 0x6f, 0xc5, 0x30, 0x01, 0x67, 0x2b, 0xfe, 0xd7, 0xab, 0x76,
    0xca, 0x82, 0xc9, 0x7d, 0xfa, 0x59, 0x47, 0xf0, 0xad, 0xd4, 0xa2, 0xaf, 0x9c, 0xa4, 0x72, 0xc0,
    0xb7, 0xfd, 0x93, 0x26, 0x36, 0x3f, 0xf7, 0xcc, 0x34, 0xa5, 0xe5, 0xf1, 0x71, 0xd8, 0x31, 0x15,
    0x04, 0xc7, 0x23, 0xc3, 0x18, 0x96, 0x05, 0x9a, 0x07, 0x12, 0x80, 0xe2, 0xeb, 0x27, 0xb2, 0x75,
    0x09, 0x83, 0x2c, 0x1a, 0x1b, 0x6e, 0x5a, 0xa0, 0x52, 0x3b, 0xd6, 0xb3, 0x29, 0xe3, 0x2f, 0x84,
    0x53, 0xd1, 0x00, 0xed, 0x20, 0xfc, 0xb1, 0x5b, 0x6a, 0xcb, 0xbe, 0x39, 0x4a, 0x4c, 0x58, 0xcf,
    0xd0, 0xef, 0xaa, 0xfb, 0x43, 0x4d, 0x33, 0x85, 0x45, 0xf9, 0x02, 0x7f, 0x50, 0x3c, 0x9f, 0xa8,
    0x51, 0xa3, 0x40, 0x8f, 0x92, 0x9d, 0x38, 0xf5, 0xbc, 0xb6, 0xda, 0x21, 0x10, 0xff, 0xf3, 0xd2,
    0xcd, 0x0c, 0x13, 0xec, 0x5f, 0x97, 0x44, 0x17, 0xc4, 0xa7, 0x7e, 0x3d, 0x64, 0x5d, 0x19, 0x73,
    0x60, 0x81, 0x4f, 0xdc, 0x22, 0x2a, 0x90, 0x88, 0x46, 0xee, 0xb8, 0x14, 0xde, 0x5e, 0x0b, 0xdb,
    0xe0, 0x32, 0x3a, 0x0a, 0x49, 0x06, 0x24, 0x5c, 0xc2, 0xd3, 0xac, 0x62, 0x91, 0x95, 0xe4, 0x79,
    0xe7, 0xc8, 0x37, 0x6d, 0x8d, 0xd5, 0x4e, 0xa9, 0x6c, 0x56, 0xf4, 0xea, 0x65, 0x7a, 0xae, 0x08,
    0xba, 0x78, 0x25, 0x2e, 0x1c, 0xa6, 0xb4, 0xc6, 0xe8, 0xdd, 0x74, 0x1f, 0x4b, 0xbd, 0x8b, 0x8a,
    0x70, 0x3e, 0xb5, 0x66, 0x48, 0x03, 0xf6, 0x0e, 0x61, 0x35, 0x57, 0xb9, 0x86, 0xc1, 0x1d, 0x9e,
    0xe1, 0xf8, 0x98, 0x11, 0x69, 0xd9, 0x8e, 0x94, 0x9b, 0x1e, 0x87, 0xe9, 0xce, 0x55, 0x28, 0xdf,
    0x8c, 0xa1, 0x89, 0x0d, 0xbf, 0xe6, 0x42, 0x68, 0x41, 0x99, 0x2d, 0x0f, 0xb0, 0x54, 0xbb, 0x16
])

# 逆 S-Box: 用于解密时的逆向字节替换
INV_SBOX = bytearray([0] * 256)
for i, v in enumerate(SBOX):
    INV_SBOX[v] = i
INV_SBOX = bytes(INV_SBOX)


# ==================== 辅助函数 ====================

def _rot_left(value, n):
    """
    32位循环左移

    用于 AES 算法中的 MixColumns 和 InvMixColumns 操作。
    CryptoJS 使用循环移位（而非逻辑移位）。

    参数:
        value: 32位整数值
        n: 移位位数 (0-31)

    返回:
        循环左移后的值
    """
    return ((value << n) | (value >> (32 - n))) & 0xffffffff


# ==================== 预计算查找表 ====================
# 用于加速 AES 加解密运算，将多步操作合并为表查询

def _compute_sub_mix_tables():
    """
    计算 SUB_MIX_0 到 SUB_MIX_3 查找表（用于加密）

    该表将 SubBytes 和 MixColumns 两步操作合并为一次表查询，
    大幅提升加密性能。

    计算逻辑:
        1. 对 S-Box 值进行 GF(2^8) 乘法
        2. 乘以 0x101 和 0x1010100 系数
        3. 循环移位生成 4 个表项

    返回:
        4 个长度为 256 的列表 (SM0, SM1, SM2, SM3)
    """
    SM0, SM1, SM2, SM3 = [], [], [], []

    for x in range(256):
        sx = SBOX[x]

        # GF(2^8) 乘以 2
        if sx < 128:
            dx = sx << 1
        else:
            dx = (sx << 1) ^ 0x11b

        # MixColumns 变换
        t = (dx * 0x101) ^ (sx * 0x1010100)

        # 生成 4 个表项（循环移位）
        SM0.append(_rot_left(t, 24))
        SM1.append(_rot_left(t, 16))
        SM2.append(_rot_left(t, 8))
        SM3.append(t & 0xffffffff)

    return SM0, SM1, SM2, SM3


# 加密用的 SUB_MIX 查找表（模块级预计算）
SUB_MIX_0, SUB_MIX_1, SUB_MIX_2, SUB_MIX_3 = _compute_sub_mix_tables()


def _compute_inv_sub_mix_tables():
    """
    计算 INV_SUB_MIX_0 到 INV_SUB_MIX_3 查找表（用于解密）

    该表将 InvSubBytes 和 InvMixColumns 两步操作合并为一次表查询。
    计算过程使用 GF(2^8) 乘法实现 InvMixColumns 变换。

    返回:
        4 个长度为 256 的列表 (tmp0, tmp1, tmp2, tmp3)
    """
    tmp0, tmp1, tmp2, tmp3 = [0] * 256, [0] * 256, [0] * 256, [0] * 256

    for i in range(256):
        sx = SBOX[i]

        # GF(2^8) 乘以 2
        if i < 128:
            dx = i << 1
        else:
            dx = (i << 1) ^ 0x11b

        # 连续乘以 2 得到 x4 和 x8
        x2 = dx
        x4 = (x2 << 1) ^ (0x11b if x2 >= 128 else 0)
        x8 = (x4 << 1) ^ (0x11b if x4 >= 128 else 0)

        # InvMixColumns 变换
        t = (x8 * 0x1010101) ^ (x4 * 0x10001) ^ (x2 * 0x101) ^ (i * 0x1010100)

        # 生成 4 个表项
        tmp0[sx] = _rot_left(t, 24)
        tmp1[sx] = _rot_left(t, 16)
        tmp2[sx] = _rot_left(t, 8)
        tmp3[sx] = t & 0xffffffff

    return tmp0, tmp1, tmp2, tmp3


# 解密用的 INV_SUB_MIX 查找表（模块级预计算）
INV_SUB_MIX_0, INV_SUB_MIX_1, INV_SUB_MIX_2, INV_SUB_MIX_3 = _compute_inv_sub_mix_tables()

# 轮常量 - 用于密钥扩展
RCON = [0x00, 0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1b, 0x36]


# ==================== AES 基本操作 ====================

def _sub_word(word: int) -> int:
    """
    对一个 32-bit word 应用 S-Box（字节替换）

    将 32 位字的每个字节通过 S-Box 进行替换。

    参数:
        word: 32 位整数

    返回:
        替换后的 32 位整数
    """
    return (SBOX[(word >> 24) & 0xff] << 24 |
            SBOX[(word >> 16) & 0xff] << 16 |
            SBOX[(word >> 8) & 0xff] << 8 |
            SBOX[word & 0xff])


def _inv_sub_word(word: int) -> int:
    """
    对一个 32-bit word 应用逆 S-Box

    将 32 位字的每个字节通过逆 S-Box 进行替换。

    参数:
        word: 32 位整数

    返回:
        替换后的 32 位整数
    """
    return (INV_SBOX[(word >> 24) & 0xff] << 24 |
            INV_SBOX[(word >> 16) & 0xff] << 16 |
            INV_SBOX[(word >> 8) & 0xff] << 8 |
            INV_SBOX[word & 0xff])


def _rot_word(word: int) -> int:
    """
    循环左移 8 位（AES KeyExpansion 中的 RotWord 操作）

    将 32 位字循环左移一个字节。

    参数:
        word: 32 位整数

    返回:
        循环左移后的 32 位整数
    """
    return ((word << 8) | (word >> 24)) & 0xffffffff


def _key_expansion(key_bytes: bytes) -> Tuple[list, list]:
    """
    从密钥生成 key schedule 和 inverse key schedule

    这是 AES 算法中最关键的步骤之一，将原始密钥扩展为多轮密钥。
    本实现严格模仿 CryptoJS 的密钥扩展逻辑，支持非标准密钥长度。

    参数:
        key_bytes: 密钥字节串（长度必须是 4 的倍数）

    返回:
        tuple: (key_schedule, inv_key_schedule)
            - key_schedule: 加密用的轮密钥数组
            - inv_key_schedule: 解密用的轮密钥数组

    注意:
        CryptoJS 的密钥扩展与标准 AES 略有不同：
        1. 支持任意 4n 字节密钥
        2. 轮数 = key_size + 6
        3. 逆密钥生成逻辑特殊（使用 INV_SUB_MIX 表）
    """
    key_size = len(key_bytes) // 4  # 密钥 32-bit word 数量
    n_rounds = key_size + 6  # 加密轮数
    ks_rows = (n_rounds + 1) * 4  # 轮密钥总行数

    # 将密钥按 4 字节分组解包为 32-bit word 数组
    key_words = []
    for i in range(key_size):
        key_words.append(struct.unpack('>I', key_bytes[i*4:(i+1)*4])[0])

    # 生成加密用的 key schedule
    key_schedule = []
    for ks_row in range(ks_rows):
        if ks_row < key_size:
            # 直接使用原始密钥
            key_schedule.append(key_words[ks_row])
        else:
            t = key_schedule[ks_row - 1]

            # 轮密钥变换
            if ks_row % key_size == 0:
                t = _rot_word(t)      # RotWord
                t = _sub_word(t)      # SubWord
                t ^= (RCON[ks_row // key_size] << 24)  # XOR 轮常量
            elif key_size > 6 and ks_row % key_size == 4:
                t = _sub_word(t)      # 大密钥额外 SubWord

            key_schedule.append(key_schedule[ks_row - key_size] ^ t)

    # 生成解密用的 inverse key schedule（模仿 CryptoJS 逻辑）
    inv_key_schedule = []
    for inv_ks_row in range(ks_rows):
        ks_row = ks_rows - inv_ks_row

        if inv_ks_row % 4:
            t = key_schedule[ks_row]
        else:
            t = key_schedule[ks_row - 4]

        if inv_ks_row < 4 or ks_row <= 4:
            # 前 4 轮和最后 4 轮直接使用
            inv_key_schedule.append(t)
        else:
            # 中间轮使用 INV_SUB_MIX 表变换
            # 等价于 InvSubBytes + InvMixColumns
            inv_key_schedule.append(
                INV_SUB_MIX_0[SBOX[(t >> 24) & 0xff]] ^
                INV_SUB_MIX_1[SBOX[(t >> 16) & 0xff]] ^
                INV_SUB_MIX_2[SBOX[(t >> 8) & 0xff]] ^
                INV_SUB_MIX_3[SBOX[t & 0xff]]
            )

    return key_schedule, inv_key_schedule


# ==================== GF(2^8) 运算 ====================

def _gf_mul(a: int, b: int) -> int:
    """
    GF(2^8) 乘法 - 有限域上的乘法运算

    用于 InvMixColumns 中的矩阵乘法。
     irreducible polynomial: x^8 + x^4 + x^3 + x + 1 (0x11b)

    参数:
        a: 8 位整数（被乘数）
        b: 8 位整数（乘数）

    返回:
        GF(2^8) 乘积（8 位整数）
    """
    p = 0
    for _ in range(8):
        if b & 1:
            p ^= a
        hi = a & 0x80
        a = (a << 1) & 0xff
        if hi:
            a ^= 0x1b
        b >>= 1
    return p


def _inv_mix_column(word: int) -> int:
    """
    InvMixColumn - AES 解密中的逆列混合操作

    对一个 32 位字进行 InvMixColumns 变换，
    使用固定矩阵 [[0e,0b,0d,09],[09,0e,0b,0d],[0d,09,0e,0b],[0b,0d,09,0e]]

    参数:
        word: 32 位整数（4 字节列）

    返回:
        变换后的 32 位整数
    """
    b0 = (word >> 24) & 0xff
    b1 = (word >> 16) & 0xff
    b2 = (word >> 8) & 0xff
    b3 = word & 0xff

    # 矩阵乘法
    r0 = _gf_mul(b0, 0x0e) ^ _gf_mul(b1, 0x0b) ^ _gf_mul(b2, 0x0d) ^ _gf_mul(b3, 0x09)
    r1 = _gf_mul(b0, 0x09) ^ _gf_mul(b1, 0x0e) ^ _gf_mul(b2, 0x0b) ^ _gf_mul(b3, 0x0d)
    r2 = _gf_mul(b0, 0x0d) ^ _gf_mul(b1, 0x09) ^ _gf_mul(b2, 0x0e) ^ _gf_mul(b3, 0x0b)
    r3 = _gf_mul(b0, 0x0b) ^ _gf_mul(b1, 0x0d) ^ _gf_mul(b2, 0x09) ^ _gf_mul(b3, 0x0e)

    return (r0 << 24) | (r1 << 16) | (r2 << 8) | r3


# ==================== 块加解密 ====================

def _encrypt_block(block: bytes, key_schedule: list, n_rounds: int) -> bytes:
    """
    加密单个 16 字节块（使用查找表方法）

    使用预计算的 SUB_MIX 表加速加密过程，
    将 SubBytes、ShiftRows 和 MixColumns 合并为一次表查询。

    加密流程:
        1. AddRoundKey - 初始轮密钥加
        2. 主轮 (n_rounds-1 次) - SubBytes + ShiftRows + MixColumns + AddRoundKey
        3. 最终轮 - SubBytes + ShiftRows + AddRoundKey（无 MixColumns）

    参数:
        block: 16 字节明文块
        key_schedule: 轮密钥数组
        n_rounds: 加密轮数

    返回:
        16 字节密文块
    """
    # 解包为 4 个 32-bit word
    words = list(struct.unpack('>4I', block))

    # AddRoundKey - 初始轮
    s0 = words[0] ^ key_schedule[0]
    s1 = words[1] ^ key_schedule[1]
    s2 = words[2] ^ key_schedule[2]
    s3 = words[3] ^ key_schedule[3]

    ks_row = 4

    # 主轮循环
    for _ in range(1, n_rounds):
        # 使用 SUB_MIX 表实现 SubBytes + ShiftRows + MixColumns
        t0 = SUB_MIX_0[(s0 >> 24) & 0xff] ^ SUB_MIX_1[(s1 >> 16) & 0xff] ^ SUB_MIX_2[(s2 >> 8) & 0xff] ^ SUB_MIX_3[s3 & 0xff] ^ key_schedule[ks_row]
        ks_row += 1
        t1 = SUB_MIX_0[(s1 >> 24) & 0xff] ^ SUB_MIX_1[(s2 >> 16) & 0xff] ^ SUB_MIX_2[(s3 >> 8) & 0xff] ^ SUB_MIX_3[s0 & 0xff] ^ key_schedule[ks_row]
        ks_row += 1
        t2 = SUB_MIX_0[(s2 >> 24) & 0xff] ^ SUB_MIX_1[(s3 >> 16) & 0xff] ^ SUB_MIX_2[(s0 >> 8) & 0xff] ^ SUB_MIX_3[s1 & 0xff] ^ key_schedule[ks_row]
        ks_row += 1
        t3 = SUB_MIX_0[(s3 >> 24) & 0xff] ^ SUB_MIX_1[(s0 >> 16) & 0xff] ^ SUB_MIX_2[(s1 >> 8) & 0xff] ^ SUB_MIX_3[s2 & 0xff] ^ key_schedule[ks_row]
        ks_row += 1

        s0, s1, s2, s3 = t0, t1, t2, t3

    # 最终轮（无 MixColumns，仅 SubBytes + ShiftRows）
    t0 = (SBOX[(s0 >> 24) & 0xff] << 24) | (SBOX[(s1 >> 16) & 0xff] << 16) | (SBOX[(s2 >> 8) & 0xff] << 8) | SBOX[s3 & 0xff]
    t0 ^= key_schedule[ks_row]
    ks_row += 1

    t1 = (SBOX[(s1 >> 24) & 0xff] << 24) | (SBOX[(s2 >> 16) & 0xff] << 16) | (SBOX[(s3 >> 8) & 0xff] << 8) | SBOX[s0 & 0xff]
    t1 ^= key_schedule[ks_row]
    ks_row += 1

    t2 = (SBOX[(s2 >> 24) & 0xff] << 24) | (SBOX[(s3 >> 16) & 0xff] << 16) | (SBOX[(s0 >> 8) & 0xff] << 8) | SBOX[s1 & 0xff]
    t2 ^= key_schedule[ks_row]
    ks_row += 1

    t3 = (SBOX[(s3 >> 24) & 0xff] << 24) | (SBOX[(s0 >> 16) & 0xff] << 16) | (SBOX[(s1 >> 8) & 0xff] << 8) | SBOX[s2 & 0xff]
    t3 ^= key_schedule[ks_row]
    ks_row += 1

    return struct.pack('>4I', t0, t1, t2, t3)


def _decrypt_block(block: bytes, inv_key_schedule: list, n_rounds: int) -> bytes:
    """
    解密单个 16 字节块（使用查找表方法）

    使用预计算的 INV_SUB_MIX 表加速解密过程。
    注意：CryptoJS 的解密实现有特殊的行交换处理。

    解密流程:
        1. 交换第 2 和第 4 行（CryptoJS 特殊处理）
        2. AddRoundKey - 初始轮密钥加
        3. 逆主轮 - InvSubBytes + InvShiftRows + InvMixColumns + AddRoundKey
        4. 最终逆轮 - InvSubBytes + InvShiftRows + AddRoundKey
        5. 再次交换回来

    参数:
        block: 16 字节密文块
        inv_key_schedule: 逆轮密钥数组
        n_rounds: 加密轮数

    返回:
        16 字节明文块
    """
    # 解包为 4 个 32-bit word
    words = list(struct.unpack('>4I', block))

    # CryptoJS 特殊处理: 交换第 2 和第 4 行
    words[1], words[3] = words[3], words[1]

    # AddRoundKey - 初始轮
    s0 = words[0] ^ inv_key_schedule[0]
    s1 = words[1] ^ inv_key_schedule[1]
    s2 = words[2] ^ inv_key_schedule[2]
    s3 = words[3] ^ inv_key_schedule[3]

    ks_row = 4

    # 逆主轮循环（使用 INV_SUB_MIX 表）
    for _ in range(1, n_rounds):
        t0 = INV_SUB_MIX_0[(s0 >> 24) & 0xff] ^ INV_SUB_MIX_1[(s1 >> 16) & 0xff] ^ INV_SUB_MIX_2[(s2 >> 8) & 0xff] ^ INV_SUB_MIX_3[s3 & 0xff] ^ inv_key_schedule[ks_row]
        ks_row += 1
        t1 = INV_SUB_MIX_0[(s1 >> 24) & 0xff] ^ INV_SUB_MIX_1[(s2 >> 16) & 0xff] ^ INV_SUB_MIX_2[(s3 >> 8) & 0xff] ^ INV_SUB_MIX_3[s0 & 0xff] ^ inv_key_schedule[ks_row]
        ks_row += 1
        t2 = INV_SUB_MIX_0[(s2 >> 24) & 0xff] ^ INV_SUB_MIX_1[(s3 >> 16) & 0xff] ^ INV_SUB_MIX_2[(s0 >> 8) & 0xff] ^ INV_SUB_MIX_3[s1 & 0xff] ^ inv_key_schedule[ks_row]
        ks_row += 1
        t3 = INV_SUB_MIX_0[(s3 >> 24) & 0xff] ^ INV_SUB_MIX_1[(s0 >> 16) & 0xff] ^ INV_SUB_MIX_2[(s1 >> 8) & 0xff] ^ INV_SUB_MIX_3[s2 & 0xff] ^ inv_key_schedule[ks_row]
        ks_row += 1

        s0, s1, s2, s3 = t0, t1, t2, t3

    # 最终逆轮（无 InvMixColumns，仅 InvSubBytes + InvShiftRows）
    t0 = (INV_SBOX[(s0 >> 24) & 0xff] << 24) | (INV_SBOX[(s1 >> 16) & 0xff] << 16) | (INV_SBOX[(s2 >> 8) & 0xff] << 8) | INV_SBOX[s3 & 0xff]
    t0 ^= inv_key_schedule[ks_row]
    ks_row += 1

    t1 = (INV_SBOX[(s1 >> 24) & 0xff] << 24) | (INV_SBOX[(s2 >> 16) & 0xff] << 16) | (INV_SBOX[(s3 >> 8) & 0xff] << 8) | INV_SBOX[s0 & 0xff]
    t1 ^= inv_key_schedule[ks_row]
    ks_row += 1

    t2 = (INV_SBOX[(s2 >> 24) & 0xff] << 24) | (INV_SBOX[(s3 >> 16) & 0xff] << 16) | (INV_SBOX[(s0 >> 8) & 0xff] << 8) | INV_SBOX[s1 & 0xff]
    t2 ^= inv_key_schedule[ks_row]
    ks_row += 1

    t3 = (INV_SBOX[(s3 >> 24) & 0xff] << 24) | (INV_SBOX[(s0 >> 16) & 0xff] << 16) | (INV_SBOX[(s1 >> 8) & 0xff] << 8) | INV_SBOX[s2 & 0xff]
    t3 ^= inv_key_schedule[ks_row]
    ks_row += 1

    # 再次交换回来
    result_words = [t0, t1, t2, t3]
    result_words[1], result_words[3] = result_words[3], result_words[1]

    return struct.pack('>4I', *result_words)


# ==================== PKCS7 填充 ====================

def _pkcs7_pad(data: bytes, block_size: int = 16) -> bytes:
    """
    PKCS7 填充 - 在数据末尾添加填充字节

    填充规则:
        - 填充内容为填充长度值
        - 填充后数据长度必须为 block_size 的倍数
        - 至少添加 1 字节填充

    示例:
        若 block_size=16，数据长度为 14 字节:
        添加 2 字节填充，值为 0x02

    参数:
        data: 原始数据
        block_size: 块大小（默认 16）

    返回:
        填充后的数据
    """
    n_padding = block_size - len(data) % block_size
    padding = bytes([n_padding] * n_padding)
    return data + padding


def _pkcs7_unpad(data: bytes, block_size: int = 16) -> bytes:
    """
    PKCS7 去填充 - 移除数据末尾的填充字节

    验证规则:
        - 最后一个字节的值为填充长度
        - 填充长度必须在 1 到 block_size 之间
        - 所有填充字节的值必须相同

    参数:
        data: 填充后的数据
        block_size: 块大小（默认 16）

    返回:
        原始数据（去除填充）

    异常:
        ValueError: 当数据为空、填充字节无效或验证失败时
    """
    if not data:
        raise ValueError("数据为空")

    n_padding = data[-1]
    if n_padding < 1 or n_padding > block_size:
        raise ValueError(f"无效的填充字节: {n_padding}")

    if data[-n_padding:] != bytes([n_padding] * n_padding):
        raise ValueError("填充验证失败")

    return data[:-n_padding]


# ==================== 主类 ====================

class CryptoJSAES:
    """
    兼容 CryptoJS 的 AES 加解密类

    支持 AES-ECB 模式的加密和解密，
    可处理非标准密钥长度（如 48 字节）。

    使用方式:
        # 创建实例
        aes = CryptoJSAES(key)

        # 加密
        ciphertext = aes.encrypt(plaintext)
        base64_ciphertext = aes.encrypt_to_base64(plaintext)

        # 解密
        plaintext = aes.decrypt(ciphertext)
        plaintext = aes.decrypt_from_base64(base64_str)

    参数:
        key: 密钥字节串，长度必须是 4 的倍数

    示例:
        >>> key = b'\x00' * 16 + b'a' * 32  # 48 字节密钥
        >>> aes = CryptoJSAES(key)
        >>> encrypted = aes.encrypt(b'Hello World')
        >>> decrypted = aes.decrypt(encrypted)
        >>> decrypted == b'Hello World'  # True
    """

    def __init__(self, key: bytes):
        """
        初始化 AES 实例

        参数:
            key: 密钥字节串

        异常:
            ValueError: 当密钥长度不是 4 的倍数时
        """
        if len(key) % 4 != 0:
            raise ValueError("密钥长度必须是 4 的倍数")

        self.key = key
        self.key_size = len(key) // 4  # 32-bit word 数量
        self.n_rounds = self.key_size + 6  # 加密轮数

        # 生成轮密钥
        self.key_schedule, self.inv_key_schedule = _key_expansion(key)

    def encrypt(self, plaintext: bytes) -> bytes:
        """
        AES-ECB 加密

        对明文进行 PKCS7 填充后，逐块加密。

        参数:
            plaintext: 明文字节串

        返回:
            密文字节串
        """
        padded = _pkcs7_pad(plaintext)
        result = b''
        for i in range(0, len(padded), 16):
            block = padded[i:i+16]
            result += _encrypt_block(block, self.key_schedule, self.n_rounds)
        return result

    def decrypt(self, ciphertext: bytes) -> bytes:
        """
        AES-ECB 解密

        逐块解密后去除 PKCS7 填充。

        参数:
            ciphertext: 密文字节串

        返回:
            明文字节串
        """
        result = b''
        for i in range(0, len(ciphertext), 16):
            block = ciphertext[i:i+16]
            result += _decrypt_block(block, self.inv_key_schedule, self.n_rounds)
        return _pkcs7_unpad(result)

    def encrypt_to_base64(self, plaintext: bytes) -> str:
        """
        加密并输出 Base64 编码字符串

        参数:
            plaintext: 明文字节串

        返回:
            Base64 编码的密文字符串
        """
        ciphertext = self.encrypt(plaintext)
        return base64.b64encode(ciphertext).decode('utf-8')

    def decrypt_from_base64(self, base64_str: str) -> bytes:
        """
        从 Base64 编码字符串解密

        参数:
            base64_str: Base64 编码的密文字符串

        返回:
            明文字节串
        """
        ciphertext = base64.b64decode(base64_str)
        return self.decrypt(ciphertext)


# ==================== 自测代码 ====================

if __name__ == '__main__':
    """
    模块自测 - 验证加解密功能的正确性

    测试用例:
        1. 加密/解密往返测试
        2. 解密 Node.js 加密的数据
        3. 使用实际 WIPO 密钥格式
    """
    print("=== 测试 CryptoJS 兼容 AES 实现 ===")

    # 测试密钥 (模拟 WIPO 使用的密钥格式：16 字节前缀 + 32 字节 search_id)
    # 前缀的真实值不入库，从环境变量 WIPO_DECRYPT_KEY_PREFIX 读取
    key_prefix = os.environ.get('WIPO_DECRYPT_KEY_PREFIX', '').encode('utf-8')[:16].ljust(16, b'\x00')
    search_id = b'a' * 32
    key_full = key_prefix + search_id  # 48 字节密钥

    target_text = b'{"test": "hello world"}'

    # 测试 1: 加密/解密往返
    print("\n测试 1: 加密/解密往返")
    aes = CryptoJSAES(key_full)
    encrypted = aes.encrypt(target_text)
    decrypted = aes.decrypt(encrypted)
    print(f"  原始数据: {target_text.decode()}")
    print(f"  解密后: {decrypted.decode()}")
    assert decrypted == target_text, "往返测试失败!"
    print(f"  ✅ 成功")

    # 测试 2: 解密来自 Node.js 的加密数据
    print("\n测试 2: 解密 Node.js 加密的数据")
    encrypted_js = "Sn92johm6DKab6f20bGMFdygTLsxiDo/OpdZ+lobWZw="

    try:
        decrypted = aes.decrypt_from_base64(encrypted_js)
        print(f"  解密结果: {decrypted.decode()}")
        print(f"  ✅ 成功")
    except Exception as e:
        print(f"  ❌ 失败: {e}")

    # 测试 3: 使用实际的 WIPO 密钥格式
    print("\n测试 3: 使用实际 WIPO 密钥格式")
    import uuid
    test_search_id = uuid.uuid4().hex
    test_key_str = os.environ.get('WIPO_DECRYPT_KEY_PREFIX', '') + test_search_id
    test_key = test_key_str.encode('utf-8')

    aes2 = CryptoJSAES(test_key)
    encrypted2 = aes2.encrypt(target_text)
    decrypted2 = aes2.decrypt(encrypted2)
    print(f"  原始数据: {target_text.decode()}")
    print(f"  解密后: {decrypted2.decode()}")
    assert decrypted2 == target_text, "往返测试失败!"
    print(f"  ✅ 成功")

    print("\n所有测试完成!")
