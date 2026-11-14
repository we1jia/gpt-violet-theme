"""生成紫龙主题的背景场景 hooks/scene.js（纯矢量 SVG 字符串），两套人物各一张：
- lady（默认）：御姐紫龙少女，素材 tools/source/dragon-girl.webp（纯黑底），按 Q 版参考图调白调淡；
- q：Q 版小紫龙，素材 tools/source/chibi.webp（透明底），本身就是淡紫配色，不调色。
云海、弯月、星光和配色两套共用。

用法（在 mod 文件夹里执行；需要用户目录里的 numpy/opencv，所以不要加 -I）:
    python3 tools/build_scene.py

为什么是纯矢量：当前 App 版本 backdrop.image 不绘制，backdrop.scene 里嵌入的位图也会被拦掉，
只有矢量图形能显示。所以人物用 tools/trace.py 从素材描摹成分层矢量路径。

为什么只用一张：每个 <Svg> 最多 131072 字符。试过拆成四张叠画（合计 34 万字符），
桌面端整张背景留空不画，单张图能正常显示，所以全部内容挤在一张里，靠省字符换精度：
- 图层按"画面里挨着的颜色排在一起"叠（trace.py 的 order='adjacent'），同样画面体积小约 20%；
- 脸单独描一层盖在上面，身体层里脸的中心挖掉不描，不重复花字符；
- evenodd 填充规则在外层 <g> 上写一次。

为什么是静态：场景按整张图绘制，任何动画都会让 App 每帧重画，持续占用 CPU，所以不加动画。

布局（每张图的根 svg 都无 viewBox、铺满窗口，坐标系一致才能对齐）：
1. 底色渐变 + 柔光（居中 slice 层）
2. 底部云海：远处几缕薄云，中景和近景各两三团疏密不一的积云，等比铺满不拉扁
3. 右侧：柔光 + 人物 + 星光和紫晶（右下对齐 meet 层，始终完整可见）
   脸单独按原图精度描摹，叠在身体上层，用径向渐变遮罩和身体自然过渡
4. 左下：弯月和星光（按 CSS 像素固定在侧栏底部，不随窗口缩放）
"""
import os
import random
import sys

import cv2
import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from trace import to_group, trace  # noqa: E402

OUT = os.path.join(HERE, '..', 'hooks', 'scene.js')
OUT_DTS = os.path.join(HERE, '..', 'hooks', 'scene.d.ts')
LIMIT = 131072
# 留 1500 字符余量，防止 App 处理时追加的样式把它挤超
BUDGET = LIMIT - 1500

# 身体层里挖掉脸中心的比例：脸层的遮罩在 68% 半径以内完全不透明，挖到 60% 看不出来
FACE_HOLE = .6

# 从细到粗挑第一个装得进一张图的。身体：描摹高度、颜色数、化简误差、最小面积；脸：倍数、颜色数、误差、最小面积
CANDIDATES = [
    ((760, 26, 1.0, 5), (1.3, 20, .65, 2)),
    ((700, 26, 1.1, 6), (1.3, 20, .65, 2)),
    ((680, 24, 1.15, 6), (1.2, 18, .7, 3)),
    ((660, 24, 1.15, 7), (1.2, 18, .7, 3)),
    ((640, 24, 1.2, 7), (1.2, 18, .7, 3)),
    ((620, 22, 1.2, 8), (1.0, 16, .8, 3)),
    ((600, 22, 1.25, 8), (1.0, 16, .8, 3)),
    ((580, 22, 1.3, 9), (1.0, 16, .8, 3)),
    ((560, 20, 1.35, 10), (1.0, 16, .8, 3)),
]

# 两套人物。face_*：脸部在原图里的范围（左、上、右、下）、脸中心、柔化椭圆半径（原图像素）；
# height/top：人物在右侧层（1600x1000）里的高度和顶边；halo：柔光中心在人物高度的哪个位置
CHARACTERS = {
    'lady': dict(scene='violet-dragon', src='dragon-girl.webp', ground='black', recolor=True,
                 face_box=(430, 150, 670, 350), face_center=(548, 252), face_radius=(108, 92),
                 height=720, top=182, halo=.23),
    # Q 版站着、头身比小，整体放矮一点，脚踩进云里；脸大，柔化椭圆也大
    'q': dict(scene='violet-chibi', src='chibi.webp', ground='alpha', recolor=False,
              face_box=(400, 305, 690, 555), face_center=(545, 430), face_radius=(140, 118),
              height=640, top=290, halo=.33),
}


def cut_out_black(path):
    """纯黑底素材：从四边漫灌接近纯黑的像素当背景，再收进 1 像素去掉黑边"""
    rgb = np.array(Image.open(path).convert('RGB'))
    dark = (rgb.max(axis=2) <= 28).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(dark, connectivity=4)
    edge = set(np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]])))
    # 碰到四边的黑块是背景；没碰到边、但面积不小的黑块是翅膀和头发之间的缝，也当背景
    bg_ids = [i for i in range(1, n) if i in edge or stats[i, cv2.CC_STAT_AREA] >= 150]
    alpha = np.where(np.isin(labels, bg_ids), 0, 255).astype(np.uint8)
    alpha = cv2.erode(alpha, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    return rgb, alpha


def cut_out_alpha(path):
    """透明底素材：直接用透明通道，半透明的边按 160 一刀切，边缘更利落"""
    rgba = np.array(Image.open(path).convert('RGBA'))
    return rgba[..., :3], np.where(rgba[..., 3] >= 160, 255, 0).astype(np.uint8)


def ellipse_mask(shape, cx, cy, rx, ry):
    h, w = shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    return ((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2 <= 1


def trace_at(im, height, k, eps, min_area):
    """按指定高度描摹，返回 (分层路径列表, 宽, 高)"""
    im = im.resize((round(im.width * height / im.height), height), Image.LANCZOS)
    return trace(np.array(im), k=k, eps=eps, min_area=min_area, keep_largest=False, order='adjacent')


class Character:
    """一套人物：抠图、身体层（挖掉脸中心）、脸层，以及在右侧层里的摆放"""

    def __init__(self, cfg):
        self.cfg = cfg
        cut = cut_out_black if cfg['ground'] == 'black' else cut_out_alpha
        rgb, alpha = cut(os.path.join(HERE, 'source', cfg['src']))
        # 去掉零碎的小块，只留主体
        n, lab, st, _ = cv2.connectedComponentsWithStats(alpha, connectivity=8)
        for i in range(1, n):
            if st[i, cv2.CC_STAT_AREA] < 400:
                alpha[lab == i] = 0
        ys, xs = np.where(alpha > 0)
        self.bbox = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
        self.full = np.dstack([rgb, alpha])
        self.face_box, self.face_center, self.face_radius = cfg['face_box'], cfg['face_center'], cfg['face_radius']

        x0, y0, x1, y1 = self.bbox
        body = self.full.copy()
        fcx, fcy = self.face_center
        hole = ellipse_mask(body.shape, fcx, fcy, self.face_radius[0] * FACE_HOLE, self.face_radius[1] * FACE_HOLE)
        body[..., 3] = np.where(hole, 0, body[..., 3])
        self.body = Image.fromarray(body[y0:y1, x0:x1], 'RGBA')

        fx0, fy0, fx1, fy1 = self.face_box
        face = self.full[fy0:fy1, fx0:fx1].copy()
        inside = ellipse_mask(face.shape, fcx - fx0, fcy - fy0, *self.face_radius)
        face[..., 3] = np.where(inside, face[..., 3], 0)
        self.face = Image.fromarray(face, 'RGBA')

        self.height, self.top = cfg['height'], cfg['top']
        self.unit = self.height / (y1 - y0)                   # 原图 1 像素 = unit 个单位
        self.width = (x1 - x0) * self.unit
        self.x0 = 1592 - self.width                          # 人物左边缘（右边留 8）
        self.dy = self.top - 262                             # 星光等装饰跟着人物一起上下移

    def colors(self, layers):
        return [(soften(c), d) for c, d in layers] if self.cfg['recolor'] else layers

    def body_group(self, layers, trace_h):
        s = self.height / trace_h
        return f'<g fill-rule="evenodd" transform="translate({self.x0:.1f} {self.top}) scale({s:.4f})">{to_group(self.colors(layers))}</g>'

    def face_group(self, layers, scale):
        fx = self.x0 + (self.face_box[0] - self.bbox[0]) * self.unit
        fy = self.top + (self.face_box[1] - self.bbox[1]) * self.unit
        return (f'<g fill-rule="evenodd" transform="translate({fx:.2f} {fy:.2f}) scale({self.unit / scale:.5f})">'
                f'<g mask="url(#fm)">{to_group(self.colors(layers))}</g></g>')

    def face_defs(self, scale):
        ecx = (self.face_center[0] - self.face_box[0]) * scale
        ecy = (self.face_center[1] - self.face_box[1]) * scale
        erx, ery = self.face_radius[0] * scale, self.face_radius[1] * scale
        return ('<radialGradient id="feather"><stop offset=".68" stop-color="#fff"/><stop offset="1" stop-color="#000"/></radialGradient>'
                f'<mask id="fm" maskContentUnits="userSpaceOnUse"><ellipse cx="{ecx:.0f}" cy="{ecy:.0f}" rx="{erx:.0f}" ry="{ery:.0f}" fill="url(#feather)"/></mask>')


# ---------- 调色：按 Q 版参考图变白、去灰、紫色变浅 ----------
# 参考 tools/source/chibi.webp（用户给的 Q 版小龙娘）：只统计人物、不算肤色，
# 亮度 L 分位（10/25/50/75/90）= 55/68/82/92/96，彩度 C = 6/9/21/34/45，三分之一接近纯白。
# 原图人物：L = 29/47/69/82/88，C = 15/19/28/39/61。两边按分位一一对应映射：
# 亮部推到接近白、发灰的淡紫去掉紫气变干净，紫色点缀保留饱和度；最暗的线条留一点深度，五官不糊。
# 肤色（暖色）不动。
L_MAP = ([0, 12, 29, 47, 69, 82, 88, 100], [25, 35, 55, 68, 82, 92, 96, 99])
C_MAP = ([0, 15, 19, 28, 39, 61, 130], [0, 6, 9, 21, 34, 45, 70])


def soften(hex_color):
    rgb = np.array([[[int(hex_color[i:i + 2], 16) for i in (1, 3, 5)]]], dtype=np.uint8)
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(float)[0, 0]
    L, a, b = lab[0] * 100 / 255, lab[1] - 128, lab[2] - 128
    C = float(np.hypot(a, b))
    hue = (np.degrees(np.arctan2(b, a)) + 360) % 360
    # 肤色：色相 20～95 的暖色且有彩度。边缘 12 度、彩度 6～10 之间渐变，免得肤色和衣服交界处断层
    edge = min(hue - 8, 107 - hue) / 12 if 8 < hue < 107 else 0.0
    skin = float(np.clip(edge, 0, 1) * np.clip((C - 6) / 4, 0, 1))
    w = 1 - skin
    if w <= 0:
        return hex_color
    L2, C2 = np.interp(L, *L_MAP), np.interp(C, *C_MAP)
    L3, C3 = L + (L2 - L) * w, C + (C2 - C) * w
    scale = C3 / C if C > 0 else 0
    out = np.array([[[L3 * 255 / 100, 128 + a * scale, 128 + b * scale]]])
    r, g, bb = cv2.cvtColor(np.clip(out, 0, 255).astype(np.uint8), cv2.COLOR_LAB2RGB)[0, 0]
    return f'#{int(r):02x}{int(g):02x}{int(bb):02x}'


# ---------- 云海 ----------

def cloud_bank(cx, base, hw, peak, seed):
    """一团积云：底部平、中间高、两头低，大朵打底，顶上叠几朵小的做出层叠感。返回圆列表"""
    rnd = random.Random(seed)
    puffs = []
    n = max(4, round(hw / 46))
    for i in range(n):
        t = -1 + 2 * (i + rnd.uniform(.25, .75)) / n
        h = peak * (.22 + .78 * max(0.0, 1 - t * t) ** .75) * rnd.uniform(.82, 1.08)
        r = max(16.0, h * rnd.uniform(.5, .66))
        puffs.append((cx + t * hw, base - h + r, r))
    for _ in range(max(2, n // 2)):
        t = rnd.uniform(-.55, .55)
        h = peak * (.22 + .78 * max(0.0, 1 - t * t) ** .75)
        r = max(12.0, h * rnd.uniform(.26, .36))
        puffs.append((cx + t * hw + rnd.uniform(-18, 18), base - h * rnd.uniform(.96, 1.06) + r, r))
    # 位置高的先画、低的后画：下面的云朵压在上面的前面，层次更像真的积云
    return sorted(puffs, key=lambda p: p[1])


def puff_circles(puffs):
    return ''.join(f'<circle cx="{x:.0f}" cy="{y:.0f}" r="{r:.0f}"/>' for x, y, r in puffs)


def flat_rect(cx, base, hw, peak):
    return f'<rect x="{cx - hw * .92:.0f}" y="{base - peak * .2:.0f}" width="{hw * 1.84:.0f}" height="{430 - base + peak * .2:.0f}"/>'


# 中景和近景的云团：中心、底边、半宽、最高处高度、随机种子（坐标系 1600x400，底边贴窗口底部）
MID_BANKS = [(470, 404, 300, 185, 3), (1180, 400, 330, 200, 5)]
NEAR_BANKS = [(140, 420, 290, 152, 7), (830, 432, 230, 96, 9), (1470, 416, 270, 168, 11)]
# 远处薄云：中心、高度、半宽、半高
WISPS = [(320, 214, 260, 15), (980, 176, 340, 13), (1430, 246, 230, 17), (700, 262, 170, 11)]


def clouds_markup():
    defs, out = [], []
    defs.append('<filter id="wb" x="-20%" y="-200%" width="140%" height="500%"><feGaussianBlur stdDeviation="5"/></filter>')
    out.append('<g fill="#fff" fill-opacity=".5" filter="url(#wb)">'
               + ''.join(f'<ellipse cx="{x}" cy="{y}" rx="{rx}" ry="{ry}"/>' for x, y, rx, ry in WISPS) + '</g>')
    # 每层：标记、云团、顶色、底色、阴影色、浓度、本体模糊、高光浓度（中景高光太亮会像发光的圆点）
    # 整团云只用一个从上到下的渐变（给每朵单独打明暗会像一串气球），边缘柔化成棉花感；
    # 底下垫一层往下错开、模糊很重的淡紫阴影，顶上叠几块模糊的白色高光，云团就有了体积
    tiers = [
        ('m', MID_BANKS, '#f6f0fe', '#d9c6f4', '#c2a7eb', .8, 3, .3),
        ('n', NEAR_BANKS, '#ffffff', '#e5d8f9', '#c8afee', 1, 1.6, .75),
    ]
    defs.append('<filter id="sh" x="-15%" y="-30%" width="130%" height="160%"><feGaussianBlur stdDeviation="9"/></filter>'
                '<filter id="hl" x="-20%" y="-40%" width="140%" height="180%"><feGaussianBlur stdDeviation="6"/></filter>')
    for tag, banks, top, bottom, shade, op, blur, glow in tiers:
        defs.append(f'<filter id="{tag}b" x="-10%" y="-20%" width="120%" height="140%"><feGaussianBlur stdDeviation="{blur}"/></filter>')
        for i, (cx, base, hw, peak, seed) in enumerate(banks):
            gid = f'{tag}{i}'
            defs.append(f'<linearGradient id="{gid}" gradientUnits="userSpaceOnUse" x1="0" y1="{base - peak:.0f}" x2="0" y2="{base - peak * .15:.0f}">'
                        f'<stop offset="0" stop-color="{top}"/><stop offset=".45" stop-color="{top}"/><stop offset="1" stop-color="{bottom}"/></linearGradient>')
            puffs = cloud_bank(cx, base, hw, peak, seed)
            shape = puff_circles(puffs) + flat_rect(cx, base, hw, peak)
            # 高光：取位置最高的几朵，缩小后往左上偏一点
            tops = sorted(puffs, key=lambda p: p[1] - p[2])[:max(3, len(puffs) // 3)]
            light = ''.join(f'<circle cx="{x - r * .18:.0f}" cy="{y - r * .28:.0f}" r="{r * .55:.0f}"/>' for x, y, r in tops)
            out.append(f'<g opacity="{op}">'
                       f'<g transform="translate(0 12)" fill="{shade}" fill-opacity=".5" filter="url(#sh)">{shape}</g>'
                       f'<g fill="url(#{gid})" filter="url(#{tag}b)">{shape}</g>'
                       f'<g fill="#fff" fill-opacity="{glow}" filter="url(#hl)">{light}</g></g>')
    # 最底下一层淡紫雾，把几团云连起来
    defs.append('<linearGradient id="haze" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#f1e9fd" stop-opacity="0"/><stop offset="1" stop-color="#efe5fd" stop-opacity=".95"/></linearGradient>')
    out.append('<rect x="-200" y="330" width="2000" height="80" fill="url(#haze)"/>')
    return ''.join(defs), ''.join(out)


# ---------- 装饰 ----------

def sparkle(cx, cy, s, fill='#ffffff', op=.9):
    """四角星光"""
    return (f'<path d="M{cx:.0f} {cy - s:.0f}Q{cx:.0f} {cy:.0f} {cx + s:.0f} {cy:.0f}Q{cx:.0f} {cy:.0f} {cx:.0f} {cy + s:.0f}'
            f'Q{cx:.0f} {cy:.0f} {cx - s:.0f} {cy:.0f}Q{cx:.0f} {cy:.0f} {cx:.0f} {cy - s:.0f}Z" fill="{fill}" fill-opacity="{op}"/>')


def crescent(cx, cy, r, shift=.52, inner=.96):
    """标准弯月：外圆半径 r，内圆往右上错开 shift*r、半径 inner*r，两圆相减。
    两个尖角取两圆交点，中间最厚处约 shift*r，两头尖、整体是一道弯弧（不是半个椭圆那种胖 D 形）"""
    r2 = r * inner
    ox, oy = r * shift, -r * .14                 # 内圆圆心相对外圆的偏移
    d = (ox * ox + oy * oy) ** .5
    a = (r * r - r2 * r2 + d * d) / (2 * d)      # 圆心连线上到交点弦的距离
    h = (r * r - a * a) ** .5
    px, py = ox * a / d, oy * a / d
    t1 = (cx + px + h * oy / d, cy + py - h * ox / d)   # 上尖角
    t2 = (cx + px - h * oy / d, cy + py + h * ox / d)   # 下尖角
    # 外弧走左边的大弧，内弧沿内圆左侧的小弧回来
    return (f'M{t1[0]:.1f} {t1[1]:.1f}A{r} {r} 0 1 0 {t2[0]:.1f} {t2[1]:.1f}'
            f'A{r2:.1f} {r2:.1f} 0 0 1 {t1[0]:.1f} {t1[1]:.1f}Z')


def gem(cx, cy, s, op=.75):
    """紫晶：菱形 + 左上亮面，呼应少女的紫晶耳坠"""
    w = s * .62
    return (f'<path d="M{cx:.0f} {cy - s:.0f}L{cx + w:.1f} {cy:.0f}L{cx:.0f} {cy + s:.0f}L{cx - w:.1f} {cy:.0f}Z" fill="url(#gem)" fill-opacity="{op}"/>'
            f'<path d="M{cx:.0f} {cy - s:.0f}L{cx - w:.1f} {cy:.0f}L{cx:.0f} {cy - s * .1:.1f}Z" fill="#fff" fill-opacity=".45"/>')


CSS = '<style>@media (max-width:760px){.side{display:none}}</style>'


def doc(defs, body):
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="100%" height="100%"><defs>{defs}</defs>{CSS}{body}</svg>'


def side(inner):
    return f'<svg class="side" width="100%" height="100%" viewBox="0 0 1600 1000" preserveAspectRatio="xMaxYMax meet">{inner}</svg>'


def base_parts(ch):
    """底图：渐变、柔光、云海、右侧柔光和装饰、侧栏弯月。返回 (defs, 窗口层, 右侧层内容, 侧栏层)"""
    cdefs, clouds = clouds_markup()
    defs = (
        '<linearGradient id="bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#f8f4fe"/><stop offset="1" stop-color="#efe6fc"/></linearGradient>'
        '<radialGradient id="rose"><stop offset="0" stop-color="#f7e3f6" stop-opacity=".85"/><stop offset="1" stop-color="#f7e3f6" stop-opacity="0"/></radialGradient>'
        '<radialGradient id="vio"><stop offset="0" stop-color="#e3d6fb" stop-opacity=".9"/><stop offset="1" stop-color="#e3d6fb" stop-opacity="0"/></radialGradient>'
        '<radialGradient id="lit"><stop offset="0" stop-color="#ffffff" stop-opacity=".65"/><stop offset="1" stop-color="#ffffff" stop-opacity="0"/></radialGradient>'
        '<radialGradient id="halo"><stop offset="0" stop-color="#f3e9ff" stop-opacity=".9"/><stop offset="1" stop-color="#f3e9ff" stop-opacity="0"/></radialGradient>'
        '<linearGradient id="moon" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#eee4fe"/><stop offset="1" stop-color="#b294ea"/></linearGradient>'
        '<linearGradient id="gem" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#e0d0fa"/><stop offset="1" stop-color="#a988e4"/></linearGradient>'
        '<filter id="soft" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="30"/></filter>'
        + cdefs)
    window = ('<rect width="100%" height="100%" fill="url(#bg)"/>'
              '<svg width="100%" height="100%" viewBox="0 0 1600 1000" preserveAspectRatio="xMidYMid slice">'
              '<circle cx="75" cy="50" r="560" fill="url(#rose)"/><circle cx="1440" cy="440" r="625" fill="url(#vio)"/><circle cx="750" cy="125" r="500" fill="url(#lit)"/></svg>'
              f'<svg y="58%" width="100%" height="42%" viewBox="0 0 1600 400" preserveAspectRatio="xMidYMax slice">{clouds}</svg>')
    # 右侧白纱只留一片模糊的柔光衬在人物后面，不画硬边，免得像一道光柱斜穿云海
    right = ('<path d="M781 1006 C1062 562 1219 162 1612 119 L1700 119 L1700 1006 Z" fill="#fff" fill-opacity=".34" filter="url(#soft)"/>'
             f'<circle cx="{ch.x0 + ch.width * .5:.0f}" cy="{ch.top + ch.height * ch.cfg["halo"]:.0f}" r="330" fill="url(#halo)"/>'
             + sparkle(1560, 205 + ch.dy, 16) + sparkle(1528, 158 + ch.dy, 8, '#d9c2fa') + sparkle(ch.x0 - 10, 330 + ch.dy, 11)
             + sparkle(ch.x0 + 18, 372 + ch.dy, 5, '#cdb2f6') + sparkle(1450, 560 + ch.dy, 6) + sparkle(ch.x0 + 60, 620 + ch.dy, 5)
             + gem(1575, 285 + ch.dy, 11) + gem(ch.x0 - 30, 470 + ch.dy, 7, .6))
    # 侧栏宽度是固定像素：用原点在左下角（y=100%）、可溢出的嵌套 svg，按 CSS 像素定位弯月（直径约 100）。
    # 侧栏上盖着 App 固定 56% 的毛玻璃；颜色按参考图调淡后仍保留一点紫，免得被毛玻璃盖没
    mx, my, r = 66, -156, 50
    left = ('<svg x="0" y="100%" width="1" height="1" overflow="visible">'
            + f'<circle cx="{mx}" cy="{my}" r="{r * 1.9:.0f}" fill="url(#halo)"/>'
            + f'<path d="{crescent(mx, my, r)}" fill="url(#moon)" stroke="#fff" stroke-opacity=".7" stroke-width="1.2" transform="rotate(-28 {mx} {my})"/>'
            + sparkle(mx + 40, my - 38, 12, '#b195ea') + sparkle(mx + 70, my + 2, 7, '#c8b2f3') + sparkle(mx + 30, my + 42, 6, '#b195ea')
            + gem(mx + 92, my - 52, 9, .9) + '</svg>')
    return defs, window, right, left


def build(ch, body_cfg, face_cfg):
    defs, window, right, left = base_parts(ch)
    h, k, eps, mn = body_cfg
    body_layers, _, th = trace_at(ch.body, h, k, eps, mn)
    fs, fk, feps, fmn = face_cfg
    face_layers, _, _ = trace_at(ch.face, round(ch.face.height * fs), fk, feps, fmn)
    return doc(defs + ch.face_defs(fs), window + side(right + ch.body_group(body_layers, th) + ch.face_group(face_layers, fs)) + left)


scenes = {}
for key, cfg in CHARACTERS.items():
    ch = Character(cfg)
    for body_cfg, face_cfg in CANDIDATES:
        svg = build(ch, body_cfg, face_cfg)
        print(f'{key} 尝试 身体{body_cfg} 脸{face_cfg}：{len(svg)} 字符')
        if len(svg) <= BUDGET:
            break
    else:
        raise SystemExit(f'{key} 场景超出 {LIMIT} 字符上限')
    assert '`' not in svg and '${' not in svg and '\\' not in svg
    scenes[cfg['scene']] = svg

with open(OUT, 'w', encoding='utf-8') as f:
    f.write('// 由 tools/build_scene.py 生成，不要手改；改布局请改脚本后重新生成\n')
    f.write('// 每套人物一张场景图，键是 $.theme.set 里的 scene 名\n')
    f.write('export const SCENES = {\n' + ''.join(f"  '{k}': `{v}`,\n" for k, v in scenes.items()) + '}\n')
with open(OUT_DTS, 'w', encoding='utf-8') as f:
    names = ' | '.join(f"'{k}'" for k in scenes)
    f.write(f'// 由 tools/build_scene.py 生成\nexport declare const SCENES: Readonly<Record<{names}, string>>\n')
print('已生成 ' + os.path.normpath(OUT) + '：' + '，'.join(f'{k} {len(v)} 字符' for k, v in scenes.items()) + f'（每张上限 {LIMIT}）')
