"""把透明底位图描摹成分层矢量路径（SVG <path> 片段）。

为什么需要：当前 App 的背景场景会拦掉 SVG 里嵌入的位图，只画矢量。
做法（"叠层"描摹，避免色块之间露缝）：
1. 只保留最大的连通主体（去掉周围散落的气泡，气泡改用矢量画）
2. 保边平滑后，用 k-means 在 Lab 空间把颜色归并成 K 种
3. 按面积从大到小排序；第 i 层的形状 = 第 i 种及之后所有颜色的并集，依次叠画，
   每个像素最终露出的就是它自己的颜色，相邻色块之间没有缝
4. 每层做轻微开闭运算去噪点，提取轮廓（含洞，evenodd 填充），按 epsilon 化简，写成相对坐标路径

图层顺序不影响画出来的颜色（每个像素最上面盖着的永远是它自己那层），只影响体积：
颜色 a、b 之间的边界会在它们之间的每一层都重描一遍。order='adjacent' 按"画面里挨着的颜色排在一起"
排序（相邻关系图的谱排序），重复描的边界少，同样画面体积约小 20%。

依赖：numpy、opencv（用户目录里的包，运行时不要加 -I）。
"""
import cv2
import numpy as np


def _largest_component(alpha_mask):
    n, labels, stats, _ = cv2.connectedComponentsWithStats(alpha_mask, connectivity=8)
    if n <= 1:
        return alpha_mask
    biggest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    return np.where(labels == biggest, 255, 0).astype(np.uint8)


def _path_from_mask(mask, eps, min_area):
    contours, _ = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    parts = []
    for c in contours:
        if abs(cv2.contourArea(c)) < min_area:
            continue
        poly = cv2.approxPolyDP(c, eps, True).reshape(-1, 2)
        if len(poly) < 3:
            continue
        x0, y0 = int(poly[0][0]), int(poly[0][1])
        seg = [f'M{x0} {y0}']
        rel = []
        px, py = x0, y0
        for x, y in poly[1:]:
            x, y = int(x), int(y)
            rel.append(f'{x - px} {y - py}')
            px, py = x, y
        # 相对坐标：逗号省略，负号可当分隔符，进一步压缩
        body = ' '.join(rel).replace(' -', '-')
        seg.append('l' + body + 'z')
        parts.append(''.join(seg))
    return ''.join(parts)


def _adjacent_order(full, k):
    """谱排序：按颜色之间共享边界的长短建图，取拉普拉斯矩阵第二小特征向量排序，挨着的颜色排得近"""
    adj = np.zeros((k, k))
    for a, b in ((full[:, :-1], full[:, 1:]), (full[:-1, :], full[1:, :])):
        m = (a >= 0) & (b >= 0) & (a != b)
        np.add.at(adj, (a[m], b[m]), 1)
    adj = adj + adj.T
    lap = np.diag(adj.sum(1)) - adj
    _, vecs = np.linalg.eigh(lap)
    order = list(np.argsort(vecs[:, 1]))
    # 方向任取其一：让面积大的一端在下面，最底层更接近整个剪影
    areas = np.array([(full == i).sum() for i in range(k)])
    half = len(order) // 2
    if areas[order[half:]].sum() > areas[order[:half]].sum():
        order.reverse()
    return order


def trace(rgba, k=24, eps=0.9, min_area=5.0, keep_largest=True, seed=7, order='area'):
    """rgba: HxWx4 uint8 数组。返回 (路径片段列表 [(hex, d)], 宽, 高)。
    order: 'area' 按面积从大到小叠；'adjacent' 按画面里的相邻关系叠，体积更小"""
    rgba = np.ascontiguousarray(rgba)
    h, w = rgba.shape[:2]
    alpha = (rgba[:, :, 3] > 128).astype(np.uint8) * 255
    if keep_largest:
        alpha = _largest_component(alpha)
    # 填掉主体内部的小透明洞（描边间隙），避免露底
    alpha = cv2.morphologyEx(alpha, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))

    rgb = np.ascontiguousarray(rgba[:, :, :3])
    smooth = cv2.bilateralFilter(rgb, 7, 40, 7)
    lab = cv2.cvtColor(smooth, cv2.COLOR_RGB2LAB).reshape(-1, 3).astype(np.float32)
    inside = alpha.reshape(-1) > 0
    samples = lab[inside]

    cv2.setRNGSeed(seed)
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 40, 0.5)
    _, labels, centers = cv2.kmeans(samples, k, None, criteria, 3, cv2.KMEANS_PP_CENTERS)
    labels = labels.reshape(-1)

    full = np.full(h * w, -1, dtype=np.int32)
    full[inside] = labels
    full = full.reshape(h, w)

    # 中心色转回 RGB
    centers_rgb = cv2.cvtColor(centers.reshape(-1, 1, 3).astype(np.uint8), cv2.COLOR_LAB2RGB).reshape(-1, 3)

    if order == 'adjacent':
        seq = _adjacent_order(full, k)
    else:
        areas = np.array([(labels == i).sum() for i in range(k)])
        seq = list(np.argsort(-areas))

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    layers = []
    remaining = alpha.copy()
    for idx in seq:
        mask = remaining.copy()
        if layers:
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        d = _path_from_mask(mask, eps, min_area)
        if d:
            r, g, b = (int(v) for v in centers_rgb[idx])
            layers.append((f'#{r:02x}{g:02x}{b:02x}', d))
        # 去掉这一种颜色，剩下的是后续各层的并集
        remaining[full == idx] = 0
    return layers, w, h


def to_group(layers):
    """填充规则 evenodd 由外层 <g fill-rule="evenodd"> 统一给，每条路径不再重复写"""
    return ''.join(f'<path fill="{c}" d="{d}"/>' for c, d in layers)
