import bpy, random
from bpy.props import FloatProperty, IntProperty, StringProperty, PointerProperty, CollectionProperty
from bpy.types import PropertyGroup
from bpy_extras.io_utils import ImportHelper

# helpers
def _prep(mat):
    mat.use_nodes = True; mat.node_tree.nodes.clear()
    return mat.node_tree.nodes, mat.node_tree.links

def _n(nodes, t, x, y):
    nd = nodes.new(t); nd.location = (x, y); return nd

def _l(links, a, a_s, b, b_s):
    links.new(a.outputs[a_s], b.inputs[b_s])

# effect helpers
def _apply_seed(nodes, seed):
    rng = random.Random(seed)
    off = (rng.uniform(-10,10), rng.uniform(-10,10), 0)
    for n in nodes:
        if n.type == 'MAPPING' and 'Location' in n.inputs:
            n.inputs['Location'].default_value = off

def _apply_color_variation(nodes, links, p):
    if p.color_variation < 0.001:
        return
    rng = random.Random(p.seed + 1)
    for node in list(nodes):
        # 只处理预设新建的节点，不碰已有 PBR
        if node.type == 'BSDF_PRINCIPLED' and node.label == '_preset_':
            bc = node.inputs['Base Color']
            hs = nodes.new('ShaderNodeHueSaturation')
            hs.location = (node.location.x - 220, node.location.y)
            hs.inputs['Hue'].default_value = 0.5 + p.color_variation * (rng.random() - 0.5)
            hs.inputs['Saturation'].default_value = 1.0
            hs.inputs['Value'].default_value = 1.0
            if bc.is_linked:
                src = bc.links[0].from_socket
                links.remove(bc.links[0])
                links.new(src, hs.inputs['Color'])
            else:
                hs.inputs['Color'].default_value = bc.default_value[:]
            links.new(hs.outputs['Color'], bc)

def _apply_edge_wear(nodes, links, p):
    if p.edge_wear < 0.01:
        return
    out = next((n for n in nodes if n.type == 'OUTPUT_MATERIAL'), None)
    if not out or not out.inputs['Surface'].is_linked:
        return
    lnk = out.inputs['Surface'].links[0]
    ex_nd, ex_sk = lnk.from_node, lnk.from_socket.name
    links.remove(lnk)
    ox, oy = out.location.x, out.location.y
    geo = nodes.new('ShaderNodeNewGeometry'); geo.location = (ox-600, oy+200)
    ramp = nodes.new('ShaderNodeValToRGB');   ramp.location = (ox-400, oy+200)
    ramp.color_ramp.elements[0].position = max(0.0, 1.0 - p.edge_wear)
    ramp.color_ramp.elements[0].color = (0,0,0,1)
    ramp.color_ramp.elements[1].position = min(1.0, 1.0 - p.edge_wear + 0.2)
    ramp.color_ramp.elements[1].color = (1,1,1,1)
    bw = nodes.new('ShaderNodeBsdfPrincipled'); bw.location = (ox-400, oy+400)
    bw.inputs['Base Color'].default_value = (0.9,0.88,0.85,1.0)
    bw.inputs['Metallic'].default_value   = 0.8
    bw.inputs['Roughness'].default_value  = 0.1
    mx = nodes.new('ShaderNodeMixShader');    mx.location = (ox-200, oy)
    links.new(geo.outputs['Pointiness'],     ramp.inputs['Fac'])
    links.new(ramp.outputs['Color'],         mx.inputs['Fac'])
    links.new(ex_nd.outputs[ex_sk],          mx.inputs[1])
    links.new(bw.outputs['BSDF'],            mx.inputs[2])
    links.new(mx.outputs['Shader'],          out.inputs['Surface'])

def _apply_normal_detail(nodes, links, p):
    if p.normal_detail < 0.01:
        return
    out = next((n for n in nodes if n.type == 'OUTPUT_MATERIAL'), None)
    if not out:
        return
    ox, oy = out.location.x - 1000, out.location.y + 600
    cd = nodes.new('ShaderNodeTexCoord');  cd.location = (ox, oy)
    ns = nodes.new('ShaderNodeTexNoise');  ns.location = (ox+200, oy)
    ns.inputs['Scale'].default_value  = 40.0
    ns.inputs['Detail'].default_value = 8.0
    nm = nodes.new('ShaderNodeNormalMap'); nm.location = (ox+400, oy)
    nm.inputs['Strength'].default_value = p.normal_detail
    links.new(cd.outputs['Object'],  ns.inputs['Vector'])
    links.new(ns.outputs['Color'],   nm.inputs['Color'])
    for node in nodes:
        if node.type == 'BSDF_PRINCIPLED' and node.label == '_preset_':
            links.new(nm.outputs['Normal'], node.inputs['Normal'])

def _apply_all(mat, p):
    nd, lk = mat.node_tree.nodes, mat.node_tree.links
    _apply_seed(nd, p.seed)
    _apply_color_variation(nd, lk, p)
    _apply_normal_detail(nd, lk, p)
    _apply_edge_wear(nd, lk, p)
# inner builders
def _build_dust(mat, p, ox=0, oy=0):
    nd, lk = mat.node_tree.nodes, mat.node_tree.links
    co = _n(nd,'ShaderNodeTexCoord',ox,oy)
    mp = _n(nd,'ShaderNodeMapping',ox+200,oy)
    mp.inputs['Scale'].default_value = (p.dust_scale,)*3
    ns = _n(nd,'ShaderNodeTexNoise',ox+400,oy)
    ns.inputs['Scale'].default_value = p.dust_scale
    ns.inputs['Detail'].default_value = 8.0
    rp = nd.new('ShaderNodeValToRGB'); rp.location=(ox+600,oy)
    rp.color_ramp.elements[0].position = max(0.0, 1.0-p.dust_amount)
    rp.color_ramp.elements[0].color = (0,0,0,1)
    rp.color_ramp.elements[1].color = (0.85,0.80,0.72,1)
    lk.new(co.outputs['Object'], mp.inputs['Vector'])
    lk.new(mp.outputs['Vector'], ns.inputs['Vector'])
    # Gravity: multiply noise mask by upward-face factor
    if p.gravity_amount > 0.01:
        grav = _gravity_mask(nd, lk, ox+400, oy-250)
        mul  = nd.new('ShaderNodeMath'); mul.location = (ox+600, oy-150)
        mul.operation = 'MULTIPLY'
        lk.new(ns.outputs['Fac'], mul.inputs[0])
        lk.new(grav,              mul.inputs[1])
        mfac = nd.new('ShaderNodeMix'); mfac.location = (ox+700, oy-150)
        mfac.data_type = 'FLOAT'
        mfac.inputs[0].default_value = p.gravity_amount
        lk.new(ns.outputs['Fac'],    mfac.inputs[2])   # A = no gravity
        lk.new(mul.outputs['Value'], mfac.inputs[3])   # B = gravity weighted
        fac_out = mfac.outputs[1]
    else:
        fac_out = ns.outputs['Fac']
    lk.new(fac_out, rp.inputs['Fac'])
    bs = _n(nd,'ShaderNodeBsdfPrincipled',ox+900,oy)
    bs.label = '_preset_'
    bs.inputs['Roughness'].default_value = 0.9
    lk.new(rp.outputs['Color'], bs.inputs['Base Color'])
    return bs, 'BSDF'

def _build_weathered(mat, p, ox=0, oy=0):
    nd, lk = mat.node_tree.nodes, mat.node_tree.links
    co = _n(nd,'ShaderNodeTexCoord',ox,oy)
    mp = _n(nd,'ShaderNodeMapping',ox+200,oy)
    mp.inputs['Scale'].default_value = (p.weathered_scale,)*3
    # Base dirt noise
    ns = _n(nd,'ShaderNodeTexNoise',ox+400,oy)
    ns.inputs['Scale'].default_value = p.weathered_scale
    ns.inputs['Detail'].default_value = 8.0
    rp = nd.new('ShaderNodeValToRGB'); rp.location=(ox+600,oy)
    rp.color_ramp.elements[0].position = max(0.0, 1.0-p.weathered_dirt)
    rp.color_ramp.elements[0].color = (0.22,0.15,0.08,1)
    rp.color_ramp.elements[1].color = (0.6,0.55,0.45,1)
    lk.new(co.outputs['Object'], mp.inputs['Vector'])
    lk.new(mp.outputs['Vector'], ns.inputs['Vector'])
    lk.new(ns.outputs['Fac'],    rp.inputs['Fac'])
    color_out = rp.outputs['Color']
    x_bs = ox+800
    # Oil paint peel: Voronoi patches reveal darker exposed layer
    if p.weathered_peel > 0.01:
        vp = _n(nd,'ShaderNodeTexVoronoi',ox+400,oy-280)
        vp.inputs['Scale'].default_value = p.weathered_scale * 0.35
        rp_peel = nd.new('ShaderNodeValToRGB'); rp_peel.location=(ox+600,oy-280)
        rp_peel.color_ramp.interpolation = 'CONSTANT'
        rp_peel.color_ramp.elements[0].position = max(0.0, 1.0-p.weathered_peel)
        rp_peel.color_ramp.elements[0].color = (0,0,0,1)
        rp_peel.color_ramp.elements[1].color = (1,1,1,1)
        mc = _cmix(nd,'MIX',ox+800,oy-140)
        mc.inputs[7].default_value = (0.20, 0.16, 0.12, 1.0)  # exposed layer
        lk.new(mp.outputs['Vector'],        vp.inputs['Vector'])
        lk.new(vp.outputs['Distance'],      rp_peel.inputs['Fac'])
        lk.new(rp.outputs['Color'],         mc.inputs[6])
        lk.new(rp_peel.outputs['Color'],    mc.inputs[0])
        color_out = mc.outputs[2]
        x_bs = ox+1050
    bs = _n(nd,'ShaderNodeBsdfPrincipled',x_bs,oy)
    bs.label = '_preset_'
    bs.inputs['Roughness'].default_value = 0.85
    lk.new(color_out, bs.inputs['Base Color'])
    # Surface cracks: Wave rings → Normal only (does not affect color)
    if p.weathered_crack > 0.01:
        wc = _n(nd,'ShaderNodeTexWave',ox+400,oy-520)
        wc.wave_type = 'RINGS'
        wc.inputs['Scale'].default_value      = p.weathered_scale * 6.0
        wc.inputs['Distortion'].default_value = 10.0
        wc.inputs['Detail'].default_value     = 4.0
        bpc = nd.new('ShaderNodeBump'); bpc.location=(ox+650,oy-520)
        bpc.inputs['Strength'].default_value = p.weathered_crack * 0.7
        lk.new(mp.outputs['Vector'],    wc.inputs['Vector'])
        lk.new(wc.outputs['Color'],     bpc.inputs['Height'])
        lk.new(bpc.outputs['Normal'],   bs.inputs['Normal'])
    return bs, 'BSDF'

def _build_scratches(mat, p, ox=0, oy=0):
    nd, lk = mat.node_tree.nodes, mat.node_tree.links
    co = _n(nd,'ShaderNodeTexCoord',ox,oy)
    if p.scratch_mask:
        img = _n(nd,'ShaderNodeTexImage',ox+200,oy)
        img.image = p.scratch_mask
        lk.new(co.outputs['UV'], img.inputs['Vector'])
        if p.mask_feather > 0.01:
            fr = nd.new('ShaderNodeValToRGB'); fr.location = (ox+400, oy)
            fr.color_ramp.elements[0].position = p.mask_feather * 0.4
            fr.color_ramp.elements[1].position = max(0.5, 1.0 - p.mask_feather * 0.4)
            lk.new(img.outputs['Color'], fr.inputs['Fac'])
            mask_out = fr.outputs['Color']
        else:
            mask_out = img.outputs['Color']
        x_ramp = ox+650
    else:
        mp = _n(nd,'ShaderNodeMapping',ox+200,oy)
        ns = _n(nd,'ShaderNodeTexNoise',ox+400,oy)
        ns.inputs['Scale'].default_value    = 30.0
        ns.inputs['Detail'].default_value   = 16.0
        ns.inputs['Roughness'].default_value= 0.8
        lk.new(co.outputs['Object'], mp.inputs['Vector'])
        lk.new(mp.outputs['Vector'], ns.inputs['Vector'])
        mask_out = ns.outputs['Fac']
        x_ramp = ox+600
    rp = nd.new('ShaderNodeValToRGB'); rp.location=(x_ramp,oy)
    rp.color_ramp.elements[0].position = max(0.0, 1.0-p.scratch_density)
    rp.color_ramp.elements[0].color = (0,0,0,1)
    b = p.scratch_bright
    rp.color_ramp.elements[1].color = (b,b,min(1.0,b+0.02),1)
    bs = _n(nd,'ShaderNodeBsdfPrincipled',x_ramp+200,oy)
    bs.label = '_preset_'
    bs.inputs['Roughness'].default_value = max(0.0, 1.0-p.scratch_depth)
    bs.inputs['Metallic'].default_value  = 0.9
    lk.new(mask_out,            rp.inputs['Fac'])
    lk.new(rp.outputs['Color'], bs.inputs['Base Color'])
    return bs, 'BSDF'

def _build_stains(mat, p, ox=0, oy=0):
    nd, lk = mat.node_tree.nodes, mat.node_tree.links
    co  = _n(nd,'ShaderNodeTexCoord',ox,oy)
    mp  = _n(nd,'ShaderNodeMapping', ox+200,oy)
    mp.inputs['Scale'].default_value = (p.stain_scale,)*3
    lk.new(co.outputs['Object'], mp.inputs['Vector'])

    stype = p.stain_type if not p.stain_mask else 'MASK'

    if p.stain_mask:
        img = _n(nd,'ShaderNodeTexImage',ox+200,oy-200)
        img.image = p.stain_mask
        lk.new(co.outputs['UV'], img.inputs['Vector'])
        if p.mask_feather > 0.01:
            fr = nd.new('ShaderNodeValToRGB'); fr.location=(ox+400,oy-200)
            fr.color_ramp.elements[0].position = p.mask_feather * 0.4
            fr.color_ramp.elements[1].position = max(0.5, 1.0-p.mask_feather*0.4)
            lk.new(img.outputs['Color'], fr.inputs['Fac'])
            mask_out = fr.outputs['Color']
        else:
            mask_out = img.outputs['Color']

    elif stype == 'WATER':
        # Voronoi distance → sharp tide-mark ring
        vr = _n(nd,'ShaderNodeTexVoronoi',ox+400,oy)
        vr.feature = 'F1'
        vr.inputs['Scale'].default_value = p.stain_scale
        rp_ring = nd.new('ShaderNodeValToRGB'); rp_ring.location=(ox+600,oy)
        rp_ring.color_ramp.interpolation = 'EASE'
        lo = max(0.0, 0.45 - p.stain_amount*0.35)
        rp_ring.color_ramp.elements[0].position = lo
        rp_ring.color_ramp.elements[0].color = (1,1,1,1)
        rp_ring.color_ramp.elements[1].position = min(0.95, lo+0.15)
        rp_ring.color_ramp.elements[1].color = (0,0,0,1)
        lk.new(mp.outputs['Vector'],     vr.inputs['Vector'])
        lk.new(vr.outputs['Distance'],   rp_ring.inputs['Fac'])
        mask_out = rp_ring.outputs['Color']

    elif stype == 'OIL':
        # Soft noise blob — irregular oil patch
        ns = _n(nd,'ShaderNodeTexNoise',ox+400,oy)
        ns.inputs['Scale'].default_value    = p.stain_scale
        ns.inputs['Detail'].default_value   = 6.0
        ns.inputs['Roughness'].default_value= 0.5
        rp_oil = nd.new('ShaderNodeValToRGB'); rp_oil.location=(ox+600,oy)
        rp_oil.color_ramp.elements[0].position = max(0.0, 1.0-p.stain_amount)
        rp_oil.color_ramp.elements[0].color = (0,0,0,1)
        rp_oil.color_ramp.elements[1].color = (1,1,1,1)
        lk.new(mp.outputs['Vector'], ns.inputs['Vector'])
        lk.new(ns.outputs['Fac'],    rp_oil.inputs['Fac'])
        mask_out = rp_oil.outputs['Color']

    else:  # RUST streak — Wave bands on Y axis
        wv = _n(nd,'ShaderNodeTexWave',ox+400,oy)
        wv.wave_type       = 'BANDS'
        wv.bands_direction = 'Y'
        wv.inputs['Scale'].default_value       = p.stain_scale * 0.5
        wv.inputs['Distortion'].default_value  = 8.0
        wv.inputs['Detail'].default_value      = 10.0
        wv.inputs['Detail Scale'].default_value= 3.0
        rp_rust = nd.new('ShaderNodeValToRGB'); rp_rust.location=(ox+600,oy)
        rp_rust.color_ramp.elements[0].position = max(0.0, 1.0-p.stain_amount)
        rp_rust.color_ramp.elements[0].color = (0,0,0,1)
        rp_rust.color_ramp.elements[1].color = (1,1,1,1)
        lk.new(mp.outputs['Vector'], wv.inputs['Vector'])
        lk.new(wv.outputs['Color'],  rp_rust.inputs['Fac'])
        mask_out = rp_rust.outputs['Color']

    # BSDF per type
    x_bs = ox+850
    bs_clean  = _n(nd,'ShaderNodeBsdfPrincipled',x_bs,oy-300)
    bs_clean.inputs['Roughness'].default_value = 0.5
    bs_stain  = _n(nd,'ShaderNodeBsdfPrincipled',x_bs,oy)
    bs_stain.label = '_preset_'
    if stype == 'OIL' or (p.stain_mask and True):
        bs_stain.inputs['Base Color'].default_value = (0.04,0.03,0.02,1.0)
        bs_stain.inputs['Roughness'].default_value  = 0.08
        bs_stain.inputs['IOR'].default_value        = 1.47
    elif stype == 'RUST':
        bs_stain.inputs['Base Color'].default_value = (0.42,0.14,0.02,1.0)
        bs_stain.inputs['Roughness'].default_value  = 0.9
        bs_stain.inputs['Metallic'].default_value   = 0.0
    else:  # WATER
        bs_stain.inputs['Base Color'].default_value = (0.28,0.20,0.14,1.0)
        bs_stain.inputs['Roughness'].default_value  = 0.75

    mix = _n(nd,'ShaderNodeMixShader',x_bs+250,oy-150)
    lk.new(mask_out,               mix.inputs['Fac'])
    lk.new(bs_clean.outputs['BSDF'],mix.inputs[1])
    lk.new(bs_stain.outputs['BSDF'],mix.inputs[2])
    return mix, 'Shader'

# displacement helper
def _add_displacement(mat, nd, lk, out, p, preset_id):
    if p.disp_scale < 0.001:
        return
    try: mat.cycles.displacement_method = 'BOTH'
    except: pass
    # Use preset-specific scale for the displacement noise pattern
    scales = {'DUST': p.dust_scale, 'WEATHERED': p.weathered_scale,
              'SCRATCHES': 25.0,    'STAINS': p.stain_scale, 'WET': p.wet_scale}
    details = {'SCRATCHES': 14.0,  'WEATHERED': 8.0}
    ox, oy = out.location.x - 800, out.location.y - 350
    co  = nd.new('ShaderNodeTexCoord');    co.location  = (ox,      oy)
    mp  = nd.new('ShaderNodeMapping');     mp.location  = (ox+200,  oy)
    ns  = nd.new('ShaderNodeTexNoise');    ns.location  = (ox+400,  oy)
    ns.inputs['Scale'].default_value   = scales.get(preset_id, 5.0)
    ns.inputs['Detail'].default_value  = details.get(preset_id, 8.0)
    ns.inputs['Roughness'].default_value = 0.7
    rp  = nd.new('ShaderNodeValToRGB');    rp.location  = (ox+600,  oy)
    rp.color_ramp.elements[0].position = 0.3
    rp.color_ramp.elements[0].color    = (0,0,0,1)
    rp.color_ramp.elements[1].position = 0.7
    rp.color_ramp.elements[1].color    = (1,1,1,1)
    disp = nd.new('ShaderNodeDisplacement'); disp.location = (ox+800, oy)
    disp.inputs['Midlevel'].default_value = 0.0
    disp.inputs['Scale'].default_value    = p.disp_scale
    lk.new(co.outputs['Object'],   mp.inputs['Vector'])
    lk.new(mp.outputs['Vector'],   ns.inputs['Vector'])
    lk.new(ns.outputs['Fac'],      rp.inputs['Fac'])
    lk.new(rp.outputs['Color'],    disp.inputs['Height'])
    lk.new(disp.outputs['Displacement'], out.inputs['Displacement'])

# wrappers
def _wrap(mat, p, fn, preset_id=''):
    nd, lk = _prep(mat)
    node, sk = fn(mat, p)
    out = nd.new('ShaderNodeOutputMaterial')
    out.location = (node.location.x+300, node.location.y)
    lk.new(node.outputs[sk], out.inputs['Surface'])
    _add_displacement(mat, nd, lk, out, p, preset_id)
    _apply_all(mat, p)

def build_dust(mat,p):      _wrap(mat,p,_build_dust,      'DUST')
def build_weathered(mat,p): _wrap(mat,p,_build_weathered, 'WEATHERED')
def build_scratches(mat,p): _wrap(mat,p,_build_scratches, 'SCRATCHES')
def build_stains(mat,p):    _wrap(mat,p,_build_stains,    'STAINS')

# layer mode
def _layer(mat, p, inner_fn, fac):
    nd, lk = mat.node_tree.nodes, mat.node_tree.links
    out = next((n for n in nd if n.type=='OUTPUT_MATERIAL'), None)
    if not out or not out.inputs['Surface'].is_linked:
        return
    lnk = out.inputs['Surface'].links[0]
    base_sk = lnk.from_socket
    lk.remove(lnk)
    new_nd, sk = inner_fn(mat, p, out.location.x-1200, out.location.y-300)
    mx = nd.new('ShaderNodeMixShader')
    mx.location = (out.location.x-200, out.location.y)
    mx.inputs['Fac'].default_value = fac
    lk.new(base_sk,          mx.inputs[1])
    lk.new(new_nd.outputs[sk], mx.inputs[2])
    lk.new(mx.outputs['Shader'], out.inputs['Surface'])
    _apply_all(mat, p)

PRESETS = [
    ('DUST',      '灰尘', build_dust,      ['dust_amount','dust_scale'],                          _build_dust),
    ('WEATHERED', '做旧', build_weathered, ['weathered_dirt','weathered_scale','weathered_peel','weathered_crack'],                  _build_weathered),
    ('SCRATCHES', '划痕', build_scratches, ['scratch_density','scratch_depth','scratch_bright'],  _build_scratches),
    ('STAINS',    '污渍', build_stains,    ['stain_type','stain_amount','stain_scale'],                        _build_stains),
]
# properties
_CFG_ATTRS = ['dust_amount','dust_scale','weathered_dirt','weathered_scale',
              'scratch_density','scratch_depth','scratch_bright',
              'stain_amount','stain_scale','layer_fac','color_variation','edge_wear']

class MaterialPresetConfig(bpy.types.PropertyGroup):
    name:            StringProperty(name='名称', default='配置1')
    dust_amount:     FloatProperty(default=0.5,  min=0.0, max=1.0)
    dust_scale:      FloatProperty(default=5.0,  min=0.1, max=50.0)
    weathered_dirt:  FloatProperty(default=0.5,  min=0.0, max=1.0)
    weathered_scale: FloatProperty(default=5.0,  min=0.1, max=50.0)
    scratch_density: FloatProperty(default=0.5,  min=0.0, max=1.0)
    scratch_depth:   FloatProperty(default=0.5,  min=0.0, max=1.0)
    scratch_bright:  FloatProperty(default=0.8,  min=0.0, max=1.0)
    stain_amount:    FloatProperty(default=0.5,  min=0.0, max=1.0)
    stain_scale:     FloatProperty(default=5.0,  min=0.1, max=50.0)
    layer_fac:       FloatProperty(default=0.5,  min=0.0, max=1.0)
    color_variation: FloatProperty(default=0.0,  min=0.0, max=0.3)
    edge_wear:       FloatProperty(default=0.0,  min=0.0, max=1.0)
    seed:            IntProperty(default=0, min=0, max=9999)

class MaterialPresetProps(bpy.types.PropertyGroup):
    dust_amount:     FloatProperty(name='灰尘覆盖量',   min=0.0, max=1.0,  default=0.5,  subtype='FACTOR')
    dust_scale:      FloatProperty(name='灰尘大小',     min=0.1, max=50.0, default=5.0)
    weathered_dirt:  FloatProperty(name='污垢强度',     min=0.0, max=1.0,  default=0.5,  subtype='FACTOR')
    weathered_scale: FloatProperty(name='特征尺寸',     min=0.1, max=50.0, default=5.0)
    weathered_peel:  FloatProperty(name='油漆剥落',     min=0.0, max=1.0,  default=0.0,  subtype='FACTOR')
    weathered_crack: FloatProperty(name='表面裂纹',     min=0.0, max=1.0,  default=0.0,  subtype='FACTOR')
    stain_type:      bpy.props.EnumProperty(name='污渍类型',
                         items=[('WATER','水渍','干涸水渍/矿物质环'),
                                ('OIL',  '油污','半透明油污光泽'),
                                ('RUST', '锈迹流痕','从高处往下流的锈迹')],
                         default='WATER')
    scratch_density: FloatProperty(name='划痕密度',     min=0.0, max=1.0,  default=0.5,  subtype='FACTOR')
    scratch_depth:   FloatProperty(name='划痕深度',     min=0.0, max=1.0,  default=0.5,  subtype='FACTOR')
    scratch_bright:  FloatProperty(name='金属亮度',     min=0.0, max=1.0,  default=0.8,  subtype='FACTOR')
    stain_amount:    FloatProperty(name='污渍量',       min=0.0, max=1.0,  default=0.5,  subtype='FACTOR')
    stain_scale:     FloatProperty(name='污渍大小',     min=0.1, max=50.0, default=5.0)
    layer_fac:       FloatProperty(name='叠加强度',     min=0.0, max=1.0,  default=0.5,  subtype='FACTOR')
    seed:            IntProperty( name='随机种子',      min=0,   max=9999, default=0)
    color_variation: FloatProperty(name='颜色扰动',     min=0.0, max=0.3,  default=0.05)
    edge_wear:       FloatProperty(name='边缘磨损',     min=0.0, max=1.0,  default=0.0,  subtype='FACTOR')
    normal_detail:   FloatProperty(name='法线细节',     min=0.0, max=1.0,  default=0.0,  subtype='FACTOR')
    target_slot:     IntProperty( name='目标材质槽',    min=0,   max=31,   default=0)
    blend_mat:       PointerProperty(name='混合材质',  type=bpy.types.Material)
    blend_fac:       FloatProperty( name='混合强度',   min=0.0, max=1.0, default=0.5, subtype='FACTOR')
    blend_mask:      PointerProperty(name='混合蒙版',  type=bpy.types.Image)
    disp_scale:      FloatProperty( name='置换强度',   min=0.0,  max=0.15, default=0.0)
    wet_amount:      FloatProperty( name='湿润量',     min=0.0,  max=1.0,  default=0.5,  subtype='FACTOR')
    wet_scale:       FloatProperty( name='水滴大小',   min=0.1,  max=30.0, default=5.0)
    gravity_amount:  FloatProperty( name='重力方向',   min=0.0,  max=1.0,  default=0.0,  subtype='FACTOR')
    mask_feather:    FloatProperty( name='蒙版羽化',   min=0.0,  max=1.0,  default=0.0,  subtype='FACTOR')
    scratch_mask:    PointerProperty(name='划痕蒙版', type=bpy.types.Image)
    stain_mask:      PointerProperty(name='污渍蒙版', type=bpy.types.Image)

# operators
def _resolve_mat(context, p):
    obj = context.active_object
    if not obj or obj.type != 'MESH':
        return None
    if p.target_slot < len(obj.material_slots):
        return obj.material_slots[p.target_slot].material
    return obj.active_material

class MATERIAL_OT_apply_preset(bpy.types.Operator):
    bl_idname = 'material.apply_preset'; bl_label = '替换'
    bl_options = {'REGISTER', 'UNDO'}
    preset_id: StringProperty()

    def execute(self, context):
        p = context.scene.mat_presets
        mat = _resolve_mat(context, p)
        if not mat:
            self.report({'WARNING'}, '请选择网格物体'); return {'CANCELLED'}
        next(x for x in PRESETS if x[0] == self.preset_id)[2](mat, p)
        return {'FINISHED'}

class MATERIAL_OT_layer_preset(bpy.types.Operator):
    bl_idname = 'material.layer_preset'; bl_label = '叠加'
    bl_options = {'REGISTER', 'UNDO'}
    preset_id: StringProperty()

    def execute(self, context):
        p = context.scene.mat_presets
        mat = _resolve_mat(context, p)
        if not mat:
            return {'CANCELLED'}
        preset = next(x for x in PRESETS if x[0] == self.preset_id)
        _layer(mat, p, preset[4], p.layer_fac)
        return {'FINISHED'}

class MATERIAL_OT_copy_to_selected(bpy.types.Operator):
    bl_idname = 'material.copy_to_selected'; bl_label = '复制材质到选中物体'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        p = context.scene.mat_presets
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            return {'CANCELLED'}
        mat = _resolve_mat(context, p)
        if not mat:
            self.report({'WARNING'}, '无材质可复制'); return {'CANCELLED'}
        count = 0
        for o in context.selected_objects:
            if o == obj or o.type != 'MESH':
                continue
            if not o.material_slots:
                o.data.materials.append(mat)
            else:
                o.material_slots[0].material = mat
            count += 1
        self.report({'INFO'}, f'已复制到 {count} 个物体')
        return {'FINISHED'}

class MATERIAL_OT_pack_as_group(bpy.types.Operator):
    bl_idname = 'material.pack_as_group'; bl_label = '封装为节点组'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            return {'CANCELLED'}
        mat = obj.active_material
        if not mat or not mat.use_nodes:
            return {'CANCELLED'}
        for n in mat.node_tree.nodes:
            n.select = (n.type != 'OUTPUT_MATERIAL')
        for area in context.screen.areas:
            if area.type == 'NODE_EDITOR':
                with context.temp_override(area=area, region=area.regions[-1]):
                    bpy.ops.node.group_make()
                self.report({'INFO'}, '已封装为节点组')
                return {'FINISHED'}
        self.report({'WARNING'}, '请先打开着色器编辑器')
        return {'CANCELLED'}

class MATERIAL_OT_save_config(bpy.types.Operator):
    bl_idname = 'material.save_config'; bl_label = '保存配置'

    def execute(self, context):
        p = context.scene.mat_presets
        name = context.scene.mat_preset_save_name
        cfgs = context.scene.mat_preset_configs
        cfg = next((c for c in cfgs if c.name == name), None) or cfgs.add()
        cfg.name = name
        for a in _CFG_ATTRS:
            setattr(cfg, a, getattr(p, a))
        cfg.seed = p.seed
        self.report({'INFO'}, f'已保存：{name}')
        return {'FINISHED'}

class MATERIAL_OT_load_config(bpy.types.Operator):
    bl_idname = 'material.load_config'; bl_label = '加载配置'

    def execute(self, context):
        cfgs = context.scene.mat_preset_configs
        idx  = context.scene.mat_preset_config_index
        if not cfgs or idx >= len(cfgs):
            return {'CANCELLED'}
        cfg = cfgs[idx]
        p = context.scene.mat_presets
        for a in _CFG_ATTRS:
            setattr(p, a, getattr(cfg, a))
        p.seed = cfg.seed
        return {'FINISHED'}

class MATERIAL_OT_delete_config(bpy.types.Operator):
    bl_idname = 'material.delete_config'; bl_label = '删除配置'

    def execute(self, context):
        cfgs = context.scene.mat_preset_configs
        idx  = context.scene.mat_preset_config_index
        if cfgs and idx < len(cfgs):
            cfgs.remove(idx)
            context.scene.mat_preset_config_index = max(0, idx-1)
        return {'FINISHED'}

class MATERIAL_OT_load_mask(bpy.types.Operator, ImportHelper):
    """从文件加载黑白蒙版贴图"""
    bl_idname = 'material.load_mask'; bl_label = '加载蒙版'
    filter_glob: StringProperty(default='*.png;*.jpg;*.jpeg;*.tga;*.tif;*.exr', options={'HIDDEN'})
    mask_type:   StringProperty(default='SCRATCH')

    def execute(self, context):
        img = bpy.data.images.load(self.filepath, check_existing=True)
        p = context.scene.mat_presets
        if self.mask_type == 'SCRATCHES':
            p.scratch_mask = img
        else:
            p.stain_mask = img
        self.report({'INFO'}, f'已加载：{img.name}')
        return {'FINISHED'}

class MATERIAL_OT_clear_mask(bpy.types.Operator):
    bl_idname = 'material.clear_mask'; bl_label = '清除蒙版'
    bl_options = {'REGISTER', 'UNDO'}
    mask_type: StringProperty(default='SCRATCHES')

    def execute(self, context):
        p = context.scene.mat_presets
        if self.mask_type == 'SCRATCHES':
            p.scratch_mask = None
        else:
            p.stain_mask = None
        return {'FINISHED'}
# panels
class MATERIAL_PT_presets(bpy.types.Panel):
    bl_label = 'xin-TextureTool'; bl_idname = 'MATERIAL_PT_presets'
    bl_space_type = 'VIEW_3D'; bl_region_type = 'UI'; bl_category = '材质预设'

    def draw(self, context):
        layout = self.layout
        obj = context.active_object
        if obj and obj.type == 'MESH':
            mat = obj.active_material
            layout.label(text=mat.name if mat else '无材质', icon='MATERIAL' if mat else 'INFO')
        else:
            layout.label(text='请选择网格物体', icon='ERROR')
        layout.operator('material.xin_about', text='了解我们', icon='URL')

def _make_sub(pid, label, props):
    # mask prop name for presets that support it
    _mask_prop = {'SCRATCHES': 'scratch_mask', 'STAINS': 'stain_mask'}.get(pid)
    _mask_type  = pid  # passed to load operator

    class Sub(bpy.types.Panel):
        bl_label = label; bl_parent_id = 'MATERIAL_PT_presets'
        bl_space_type = 'VIEW_3D'; bl_region_type = 'UI'
        bl_options = {'DEFAULT_CLOSED'}
        def draw(self, context):
            p = context.scene.mat_presets
            col = self.layout.column(align=True)
            for a in props:
                col.prop(p, a, slider=True)
            if _mask_prop:
                self.layout.separator()
                row = self.layout.row(align=True)
                row.prop_search(p, _mask_prop, bpy.data, 'images', text='蒙版')
                op = row.operator('material.load_mask', text='', icon='FILEBROWSER')
                op.mask_type = _mask_type
                if getattr(p, _mask_prop):
                    clear = self.layout.operator('material.clear_mask', text='清除蒙版', icon='X')
                    clear.mask_type = _mask_type
            self.layout.separator()
            row = self.layout.row(align=True)
            op1 = row.operator('material.apply_preset', text='替换'); op1.preset_id = pid
            op2 = row.operator('material.layer_preset', text='叠加'); op2.preset_id = pid
            op3 = row.operator('material.modify_pbr',    text='改PBR');  op3.preset_id = pid
    Sub.__name__ = Sub.bl_idname = f'MATERIAL_PT_{pid.lower()}'
    return Sub

_sub_panels = [_make_sub(pid, lbl, props) for pid, lbl, _, props, __ in PRESETS]

class MATERIAL_PT_effects(bpy.types.Panel):
    bl_label = '④ 效果增强'; bl_idname = 'MATERIAL_PT_effects'
    bl_parent_id = 'MATERIAL_PT_presets'
    bl_space_type = 'VIEW_3D'; bl_region_type = 'UI'; bl_options = {'DEFAULT_CLOSED'}
    def draw(self, context):
        col = self.layout.column(align=True)
        p = context.scene.mat_presets
        col.prop(p, 'seed')
        col.separator()
        col.prop(p, 'gravity_amount',  slider=True)
        col.prop(p, 'color_variation', slider=True)
        col.prop(p, 'edge_wear',       slider=True)
        col.prop(p, 'normal_detail',   slider=True)
        col.separator()
        col.prop(p, 'disp_scale')
        col.prop(p, 'mask_feather',    slider=True)

class MATERIAL_PT_configs(bpy.types.Panel):
    bl_label = '⑤ 工具与管理'; bl_idname = 'MATERIAL_PT_configs'
    bl_parent_id = 'MATERIAL_PT_presets'
    bl_space_type = 'VIEW_3D'; bl_region_type = 'UI'; bl_options = {'DEFAULT_CLOSED'}
    def draw(self, context):
        layout = self.layout
        p = context.scene.mat_presets
        col = layout.column(align=True)
        col.prop(p, 'target_slot')
        row = col.row(align=True)
        row.operator('material.copy_to_selected', icon='COPYDOWN')
        row.operator('material.pack_as_group',    icon='NODETREE')
        layout.separator()
        layout.label(text='参数配置库', icon='BOOKMARKS')
        layout.prop(context.scene, 'mat_preset_save_name')
        layout.operator('material.save_config', text='保存当前参数')
        layout.template_list('UI_UL_list', 'mat_cfgs',
            context.scene, 'mat_preset_configs',
            context.scene, 'mat_preset_config_index')
        row = layout.row(align=True)
        row.operator('material.load_config',   text='加载')
        row.operator('material.delete_config', text='删除', icon='TRASH')

# About operator
class MATERIAL_OT_about(bpy.types.Operator):
    bl_idname = 'material.xin_about'; bl_label = '了解我们'
    def execute(self, context):
        import webbrowser; webbrowser.open('https://www.xinlab.cc')
        return {'FINISHED'}

# PBR channel modification helpers
def _find_bsdf(mat):
    """Find Principled BSDF, traversing MixShader chains."""
    nd = mat.node_tree.nodes
    out = next((n for n in nd if n.type == 'OUTPUT_MATERIAL'), None)
    if not out or not out.inputs['Surface'].is_linked:
        return None
    def _walk(node, depth=0):
        if depth > 6: return None
        if node.type == 'BSDF_PRINCIPLED': return node
        if node.type == 'MIX_SHADER':
            for i in (1, 2):
                if node.inputs[i].is_linked:
                    r = _walk(node.inputs[i].links[0].from_node, depth+1)
                    if r: return r
        return None
    return _walk(out.inputs['Surface'].links[0].from_node)

def _insert_before(lk, sock, node, in_idx, out_idx, fallback=None):
    """Route existing socket source through node; if unlinked, use fallback."""
    if sock.is_linked:
        src = sock.links[0].from_socket
        lk.remove(sock.links[0])
        lk.new(src, node.inputs[in_idx])
    elif fallback is not None:
        try: node.inputs[in_idx].default_value = fallback
        except: pass
    lk.new(node.outputs[out_idx], sock)

def _cmix(nd, blend, x, y):
    """ShaderNodeMix (RGBA) — Blender 4+/5+. Factor=inputs[0], A=inputs[6], B=inputs[7], Result=outputs[2]"""
    n = nd.new('ShaderNodeMix'); n.location = (x, y)
    n.data_type = 'RGBA'; n.blend_type = blend
    return n

def _noise_mask(nd, lk, scale, x, y):
    co = nd.new('ShaderNodeTexCoord');  co.location = (x-400, y)
    mp = nd.new('ShaderNodeMapping');   mp.location = (x-200, y)
    ns = nd.new('ShaderNodeTexNoise');  ns.location = (x, y)
    ns.inputs['Scale'].default_value  = scale
    ns.inputs['Detail'].default_value = 8.0
    lk.new(co.outputs['Object'], mp.inputs['Vector'])
    lk.new(mp.outputs['Vector'], ns.inputs['Vector'])
    return ns.outputs['Fac']

def _modify_pbr(mat, p, preset_id):
    bsdf = _find_bsdf(mat)
    if not bsdf:
        return False
    nd, lk = mat.node_tree.nodes, mat.node_tree.links
    bx, by = bsdf.location.x, bsdf.location.y

    if preset_id == 'WEATHERED':
        d = p.weathered_dirt
        fac = _noise_mask(nd, lk, p.weathered_scale, bx-900, by-300)
        # Roughness += noise * dirt
        mr = nd.new('ShaderNodeMath'); mr.location = (bx-180, by-120)
        mr.operation = 'ADD'
        _insert_before(lk, bsdf.inputs['Roughness'], mr, 0, 'Value',
                       fallback=bsdf.inputs['Roughness'].default_value)
        lk.new(fac, mr.inputs[1])
        # Base Color *= dirt color
        mc = _cmix(nd, 'MULTIPLY', bx-180, by+80)
        mc.inputs[0].default_value = d * 0.8
        mc.inputs[7].default_value = (0.22, 0.15, 0.08, 1.0)
        _insert_before(lk, bsdf.inputs['Base Color'], mc, 6, 2,
                       fallback=(0.5,0.5,0.5,1.0))

    elif preset_id == 'SCRATCHES':
        if p.scratch_mask:
            img = nd.new('ShaderNodeTexImage'); img.location = (bx-600, by)
            img.image = p.scratch_mask
            co = nd.new('ShaderNodeTexCoord'); co.location = (bx-800, by)
            lk.new(co.outputs['UV'], img.inputs['Vector'])
            s_fac = img.outputs['Color']
        else:
            s_fac = _noise_mask(nd, lk, p.scratch_density * 20, bx-900, by)
        # Roughness = max(existing, scratch)
        mr = nd.new('ShaderNodeMath'); mr.location = (bx-180, by-120)
        mr.operation = 'MAXIMUM'
        mr.inputs[1].default_value = p.scratch_depth
        _insert_before(lk, bsdf.inputs['Roughness'], mr, 0, 'Value',
                       fallback=bsdf.inputs['Roughness'].default_value)
        lk.new(s_fac, mr.inputs[1])
        # Normal += scratch bump
        bp = nd.new('ShaderNodeBump'); bp.location = (bx-180, by-280)
        bp.inputs['Strength'].default_value = p.scratch_depth * 0.5
        lk.new(s_fac, bp.inputs['Height'])
        _insert_before(lk, bsdf.inputs['Normal'], bp, 'Normal', 'Normal')

    elif preset_id == 'STAINS':
        if p.stain_mask:
            img = nd.new('ShaderNodeTexImage'); img.location = (bx-600, by)
            img.image = p.stain_mask
            co = nd.new('ShaderNodeTexCoord'); co.location = (bx-800, by)
            lk.new(co.outputs['UV'], img.inputs['Vector'])
            st_fac = img.outputs['Color']
        else:
            st_fac = _noise_mask(nd, lk, p.stain_scale, bx-900, by)
        # Base Color *= stain color
        mc = _cmix(nd, 'MULTIPLY', bx-180, by+80)
        mc.inputs[7].default_value = (0.35, 0.20, 0.05, 1.0)
        lk.new(st_fac, mc.inputs[0])
        _insert_before(lk, bsdf.inputs['Base Color'], mc, 6, 2,
                       fallback=(0.5,0.5,0.5,1.0))
        # Roughness += stain
        mr = nd.new('ShaderNodeMath'); mr.location = (bx-180, by-120)
        mr.operation = 'ADD'; mr.inputs[1].default_value = p.stain_amount * 0.3
        _insert_before(lk, bsdf.inputs['Roughness'], mr, 0, 'Value',
                       fallback=bsdf.inputs['Roughness'].default_value)

    elif preset_id == 'DUST':
        geo = nd.new('ShaderNodeNewGeometry'); geo.location = (bx-900, by-350)
        sep = nd.new('ShaderNodeSeparateXYZ'); sep.location = (bx-700, by-350)
        clp = nd.new('ShaderNodeMath');        clp.location = (bx-500, by-350)
        clp.operation = 'MAXIMUM'; clp.inputs[1].default_value = 0.0
        mul = nd.new('ShaderNodeMath');        mul.location = (bx-300, by-350)
        mul.operation = 'MULTIPLY'; mul.inputs[1].default_value = p.dust_amount
        lk.new(geo.outputs['Normal'],   sep.inputs['Vector'])
        lk.new(sep.outputs['Z'],        clp.inputs[0])
        lk.new(clp.outputs['Value'],    mul.inputs[0])
        d_fac = mul.outputs['Value']
        # Base Color -> mix in dust grey
        mc = _cmix(nd, 'MIX', bx-180, by+80)
        mc.inputs[7].default_value = (0.85, 0.82, 0.76, 1.0)
        lk.new(d_fac, mc.inputs[0])
        _insert_before(lk, bsdf.inputs['Base Color'], mc, 6, 2,
                       fallback=(0.5,0.5,0.5,1.0))
        # Roughness +=
        mr = nd.new('ShaderNodeMath'); mr.location = (bx-180, by-120)
        mr.operation = 'ADD'
        _insert_before(lk, bsdf.inputs['Roughness'], mr, 0, 'Value',
                       fallback=bsdf.inputs['Roughness'].default_value)
        lk.new(d_fac, mr.inputs[1])
        # Metallic *=
        mm = nd.new('ShaderNodeMath'); mm.location = (bx-180, by-240)
        mm.operation = 'MULTIPLY'; mm.inputs[1].default_value = max(0.0, 1.0-p.dust_amount)
        _insert_before(lk, bsdf.inputs['Metallic'], mm, 0, 'Value',
                       fallback=bsdf.inputs['Metallic'].default_value)
    return True


class MATERIAL_OT_modify_pbr(bpy.types.Operator):
    """直接修改已有 PBR 材质通道（基础色/粗糙度/法线/金属度）"""
    bl_idname = 'material.modify_pbr'; bl_label = '改PBR通道'
    bl_options = {'REGISTER', 'UNDO'}
    preset_id: StringProperty()

    def execute(self, context):
        p = context.scene.mat_presets
        mat = _resolve_mat(context, p)
        if not mat:
            self.report({'WARNING'}, '请选择网格物体'); return {'CANCELLED'}
        if not _modify_pbr(mat, p, self.preset_id):
            self.report({'WARNING'}, '未找到 Principled BSDF，请确认材质已有PBR节点')
            return {'CANCELLED'}
        return {'FINISHED'}
# ── PBR 材质混合 ──────────────────────────────────────────────────────────────

def _blend_pbr(mat_a, p):
    bsdf_a = _find_bsdf(mat_a)
    if not bsdf_a:
        return False

    mat_b = p.blend_mat
    nd, lk = mat_a.node_tree.nodes, mat_a.node_tree.links
    bx, by = bsdf_a.location.x, bsdf_a.location.y

    # --- build mask/fac socket ---
    if p.blend_mask:
        co  = nd.new('ShaderNodeTexCoord');  co.location  = (bx-800, by-500)
        img = nd.new('ShaderNodeTexImage');  img.location = (bx-600, by-500)
        img.image = p.blend_mask
        lk.new(co.outputs['UV'], img.inputs['Vector'])
        fac_sock = img.outputs['Color']
        use_sock = True
    else:
        use_sock = False

    # --- read material B BSDF values (or defaults) ---
    b_vals = {'Base Color':(0.5,0.5,0.5,1.0), 'Roughness':0.5,
              'Metallic':0.0, 'Normal':None}
    bsdf_b_src = _find_bsdf(mat_b) if mat_b and mat_b.use_nodes else None
    if bsdf_b_src:
        for k in ('Base Color','Roughness','Metallic'):
            inp = bsdf_b_src.inputs.get(k)
            if inp and not inp.is_linked:
                b_vals[k] = inp.default_value

    # helper: create per-channel mix node
    def _ch_mix(sock_name, in_idx, out_idx, b_val, x_off, y_off, blend_type='MIX'):
        sock = bsdf_a.inputs.get(sock_name)
        if sock is None:
            return
        if sock_name == 'Base Color':
            mn = _cmix(nd, blend_type, bx+x_off, by+y_off)
            mn.inputs[7].default_value = b_val          # B channel
            if use_sock:
                lk.new(fac_sock, mn.inputs[0])
            else:
                mn.inputs[0].default_value = p.blend_fac
            _insert_before(lk, sock, mn, 6, 2, fallback=(0.5,0.5,0.5,1.0))
        else:
            mn = nd.new('ShaderNodeMix'); mn.location = (bx+x_off, by+y_off)
            mn.data_type = 'FLOAT'
            try: mn.inputs[1].default_value = b_val
            except: pass
            if use_sock:
                lk.new(fac_sock, mn.inputs[0])
            else:
                mn.inputs[0].default_value = p.blend_fac
            _insert_before(lk, sock, mn, 2, 3, fallback=sock.default_value)

    _ch_mix('Base Color', 6, 2, b_vals['Base Color'],  -220,  120)
    _ch_mix('Roughness',  2, 3, b_vals['Roughness'],   -220,  -80)
    _ch_mix('Metallic',   2, 3, b_vals['Metallic'],    -220, -200)
    return True


class MATERIAL_OT_blend_pbr(bpy.types.Operator):
    """按通道混合当前 PBR 材质与目标材质"""
    bl_idname = 'material.blend_pbr'; bl_label = '执行混合'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        p = context.scene.mat_presets
        mat_a = _resolve_mat(context, p)
        if not mat_a:
            self.report({'WARNING'}, '请选择网格物体'); return {'CANCELLED'}
        if not p.blend_mat:
            self.report({'WARNING'}, '请先选择目标混合材质'); return {'CANCELLED'}
        if not _blend_pbr(mat_a, p):
            self.report({'WARNING'}, '未找到 Principled BSDF'); return {'CANCELLED'}
        return {'FINISHED'}


class MATERIAL_OT_load_blend_mask(bpy.types.Operator, ImportHelper):
    """加载混合蒙版图片"""
    bl_idname = 'material.load_blend_mask'; bl_label = '加载混合蒙版'
    filter_glob: StringProperty(default='*.png;*.jpg;*.jpeg;*.tga;*.tif;*.exr', options={'HIDDEN'})

    def execute(self, context):
        img = bpy.data.images.load(self.filepath, check_existing=True)
        img.colorspace_settings.name = 'Non-Color'
        context.scene.mat_presets.blend_mask = img
        self.report({'INFO'}, f'已加载：{img.name}')
        return {'FINISHED'}


class MATERIAL_PT_blend(bpy.types.Panel):
    bl_label = '③ PBR材质混合'; bl_idname = 'MATERIAL_PT_blend'
    bl_parent_id = 'MATERIAL_PT_presets'
    bl_space_type = 'VIEW_3D'; bl_region_type = 'UI'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        p = context.scene.mat_presets
        layout.prop(p, 'layer_fac', slider=True)
        layout.separator()
        layout.prop(p, 'blend_mat')
        layout.separator()
        row = layout.row(align=True)
        row.prop_search(p, 'blend_mask', bpy.data, 'images', text='混合蒙版')
        row.operator('material.load_blend_mask', text='', icon='FILEBROWSER')
        if p.blend_mask:
            layout.operator('material.clear_blend_mask', text='清除蒙版', icon='X')
        else:
            layout.prop(p, 'blend_fac', slider=True)
        layout.separator()
        layout.operator('material.blend_pbr', icon='MATERIAL')


class MATERIAL_OT_clear_blend_mask(bpy.types.Operator):
    bl_idname = 'material.clear_blend_mask'; bl_label = '清除混合蒙版'
    bl_options = {'REGISTER', 'UNDO'}
    def execute(self, context):
        context.scene.mat_presets.blend_mask = None
        return {'FINISHED'}

# register
classes = (
    MATERIAL_OT_about,
    MaterialPresetConfig, MaterialPresetProps,
    MATERIAL_OT_apply_preset, MATERIAL_OT_layer_preset,
    MATERIAL_OT_copy_to_selected, MATERIAL_OT_pack_as_group,
    MATERIAL_OT_save_config, MATERIAL_OT_load_config, MATERIAL_OT_delete_config,
    MATERIAL_OT_load_mask, MATERIAL_OT_clear_mask, MATERIAL_OT_modify_pbr,
    MATERIAL_OT_blend_pbr, MATERIAL_OT_load_blend_mask, MATERIAL_OT_clear_blend_mask,
    MATERIAL_PT_presets, *_sub_panels, MATERIAL_PT_effects, MATERIAL_PT_configs, MATERIAL_PT_blend,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.utils.register_class(_wet_panel)
    bpy.types.Scene.mat_presets            = PointerProperty(type=MaterialPresetProps)
    bpy.types.Scene.mat_preset_configs     = CollectionProperty(type=MaterialPresetConfig)
    bpy.types.Scene.mat_preset_config_index= IntProperty(default=0)
    bpy.types.Scene.mat_preset_save_name   = StringProperty(name='保存名称', default='配置1')

def unregister():
    for attr in ('mat_presets','mat_preset_configs','mat_preset_config_index','mat_preset_save_name'):
        delattr(bpy.types.Scene, attr)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    bpy.utils.unregister_class(_wet_panel)

if __name__ == '__main__':
    register()
# ── 湿润预设 ──────────────────────────────────────────────────────────────────

def _build_wet(mat, p, ox=0, oy=0):
    nd, lk = mat.node_tree.nodes, mat.node_tree.links
    co  = _n(nd,'ShaderNodeTexCoord',     ox,      oy)
    mp  = _n(nd,'ShaderNodeMapping',      ox+200,  oy)
    voro= _n(nd,'ShaderNodeTexVoronoi',   ox+400,  oy)
    voro.feature = 'F1'
    voro.inputs['Scale'].default_value = p.wet_scale
    rp  = nd.new('ShaderNodeValToRGB');   rp.location = (ox+600, oy)
    rp.color_ramp.interpolation = 'EASE'
    rp.color_ramp.elements[0].position = max(0.0, 1.0-p.wet_amount-0.1)
    rp.color_ramp.elements[0].color = (0,0,0,1)
    rp.color_ramp.elements[1].position = min(1.0, 1.0-p.wet_amount+0.1)
    rp.color_ramp.elements[1].color = (1,1,1,1)
    # Wet BSDF: very smooth, slightly dark
    bs  = _n(nd,'ShaderNodeBsdfPrincipled', ox+800, oy)
    bs.label = '_preset_'
    bs.inputs['Base Color'].default_value  = (0.08, 0.1, 0.12, 1.0)
    bs.inputs['Roughness'].default_value   = 0.02
    bs.inputs['IOR'].default_value         = 1.333
    for nm in ('Transmission Weight', 'Transmission'):
        if nm in bs.inputs: bs.inputs[nm].default_value = 0.1; break
    # Mix between base-grey and wet BSDF using the voronoi mask
    mix = _n(nd,'ShaderNodeMixShader', ox+1000, oy)
    base_bs = _n(nd,'ShaderNodeBsdfPrincipled', ox+800, oy-300)
    base_bs.label = '_preset_'
    base_bs.inputs['Roughness'].default_value = 0.5
    lk.new(co.outputs['Object'],      mp.inputs['Vector'])
    lk.new(mp.outputs['Vector'],      voro.inputs['Vector'])
    lk.new(voro.outputs['Distance'],  rp.inputs['Fac'])
    lk.new(rp.outputs['Color'],       mix.inputs['Fac'])
    lk.new(base_bs.outputs['BSDF'],   mix.inputs[1])
    lk.new(bs.outputs['BSDF'],        mix.inputs[2])
    return mix, 'Shader'

def build_wet(mat, p): _wrap(mat, p, _build_wet, 'WET')

PRESETS.append(('WET', '湿润', build_wet, ['wet_amount','wet_scale'], _build_wet))
_wet_panel = _make_sub('WET', '湿润', ['wet_amount','wet_scale'])


# ── 重力方向辅助 ──────────────────────────────────────────────────────────────

def _gravity_mask(nd, lk, x, y):
    """Returns a Fac socket: 1.0 on upward faces (world Z), 0.0 on downward."""
    geo = nd.new('ShaderNodeNewGeometry'); geo.location = (x,      y)
    sep = nd.new('ShaderNodeSeparateXYZ'); sep.location = (x+200,  y)
    clp = nd.new('ShaderNodeMath');        clp.location = (x+400,  y)
    clp.operation = 'MAXIMUM'; clp.inputs[1].default_value = 0.0
    lk.new(geo.outputs['Normal'], sep.inputs['Vector'])
    lk.new(sep.outputs['Z'],      clp.inputs[0])
    return clp.outputs['Value']