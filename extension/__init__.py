import bpy
from bpy.types import Operator, Panel, UIList, PropertyGroup
import numpy as np
import os
import psd_tools
from bpy_extras.io_utils import ImportHelper
from bpy.props import StringProperty, BoolProperty, EnumProperty, IntProperty, CollectionProperty, PointerProperty, FloatProperty
from mathutils import Vector
import math
from pathlib import Path
from PIL import Image


def camera_object_poll(self, object):
	return object.type == "CAMERA"

def load_layers_poll(context):
	return create_planes_poll(context) and len(context.scene.camap_layers) > 0

def create_planes_poll(context):
	# ext = os.path.splitext(context.scene.camap_file)[1]
	# if ext != ".psd":
	# 	return False

	if context.scene.camap_camera is None:
		return False

	if context.scene.camap_camera.type != "CAMERA":
		return False

	return True


def select_plane_poll(context, item):
	if context.object is not None:
		if context.object.mode != "OBJECT" :
			return False		

	if item.layer_object is not None:
		return item.layer_object.name in context.view_layer.objects

	return False


def load_new_camap_file(self, context):
	scene = context.scene
	# scene.camap_layers.clear()

	# Change name in the image list
	selected_file = scene.camap_files[scene.camap_file_index]
	selected_image_path_str = selected_file.image_path
	if not selected_image_path_str:
		selected_file.file_name = "New File"
		return

	remove_file_layers(selected_file)

	selected_image_path = Path(selected_image_path_str)
	selected_file.file_name = selected_image_path.stem

	if selected_image_path.suffix.lower() == ".psd":
		# Add layers in layers list
		psd = psd_tools.PSDImage.open(selected_image_path)

		scene.camap_layer_count = len([layer for layer in psd.descendants() if layer.kind == "pixel"])

		i = 0
		for layer_id, layer in enumerate(psd.descendants()):
			if layer.kind == "pixel":
				item = scene.camap_layers.add()
				item.layer_name = layer.name
				item.file = selected_file
				item.layer_id = len(scene.camap_layers) + layer_id
				item.psd_layer_id = layer_id
				item.layer_object = None
				item.layer_distance = 1 - (i / (scene.camap_layer_count - 1))
				item.layer_visibility = True

				i += 1

	elif selected_image_path.suffix.lower() in (".png", ".jpg", ".jpeg"):
		item = scene.camap_layers.add()
		item.layer_name = selected_image_path.stem
		item.file = selected_file
		item.layer_id = len(scene.camap_layers)
		item.psd_layer_id = -1
		item.layer_object = None
		item.layer_distance = 0.5
		item.layer_visibility = True


def remove_file_layers(file):
	scene = bpy.context.scene
	remove_ids_list = []
	for i, layer in enumerate(scene.camap_layers):
		if layer.file == file:
			remove_ids_list.append(i)

	for layer_id in sorted(remove_ids_list, reverse=True):
		scene.camap_layers.remove(layer_id)


def get_layers_from_file(file):
	scene = bpy.context.scene
	layers_list = []
	for layer_item in scene.camap_layers:
		if layer_item.file == file:
			layers_list.append(layer_item)

	return layers_list


def apply_modifier(target_object, modifier):
	active_obj = bpy.context.view_layer.objects.active
	selected_objects = bpy.context.selected_objects

	bpy.ops.object.select_all(action="DESELECT")
	target_object.select_set(True)
	bpy.context.view_layer.objects.active = target_object
	bpy.ops.object.modifier_apply(modifier=modifier.name)

	bpy.ops.object.select_all(action="DESELECT")
	for obj in selected_objects:
		obj.select_set(True)
	bpy.context.view_layer.objects.active = active_obj


def create_plane(name, bbox, camera, distance):
	cam_matrix = camera.matrix_world
	cam_angle = camera.data.angle / 2

	vec_x = Vector(cam_matrix.col[0][:3])
	vec_y = Vector(cam_matrix.col[1][:3])
	vec_z = Vector(cam_matrix.col[2][:3]) * -1
	vec_pos = Vector(cam_matrix.col[3][:3])
	
	res_x = bpy.context.scene.render.resolution_x
	res_y = bpy.context.scene.render.resolution_y

	if res_x > res_y:
		ratio = res_y / res_x
		mult_x = distance * math.tan(cam_angle)
		mult_y = mult_x * ratio
	else:
		ratio = res_x / res_y
		mult_y = distance * math.tan(cam_angle)
		mult_x = mult_y * ratio

	vec_start = vec_z * distance - vec_x * mult_x + vec_y * mult_y

	verts = [
		vec_pos + vec_start + vec_x * bbox[0] * mult_x * 2 - vec_y * bbox[3] * mult_y * 2,
		vec_pos + vec_start + vec_x * bbox[2] * mult_x * 2 - vec_y * bbox[3] * mult_y * 2,
		vec_pos + vec_start + vec_x * bbox[2] * mult_x * 2 - vec_y * bbox[1] * mult_y * 2,
		vec_pos + vec_start + vec_x * bbox[0] * mult_x * 2 - vec_y * bbox[1] * mult_y * 2
	]

	faces = [(0, 1, 2, 3)]

	plane_data = bpy.data.meshes.get(name)
	if plane_data is None:
		plane_data = bpy.data.meshes.new(name=name)
		plane_data.from_pydata(verts, [], faces)

		uv_layer = plane_data.uv_layers.new()
		uv_layer.data[0].uv = (0.0, 0.0)
		uv_layer.data[1].uv = (1.0, 0.0)
		uv_layer.data[2].uv = (1.0, 1.0)
		uv_layer.data[3].uv = (0.0, 1.0)

	plane_obj = bpy.data.objects.get(name)
	if plane_obj is None:
		plane_obj = bpy.data.objects.new(name=name, object_data=plane_data)

		collec = bpy.context.collection
		collec.objects.link(plane_obj)

	plane_obj.parent = camera
	plane_obj.matrix_parent_inverse = camera.matrix_world.inverted()

	return plane_obj


def create_adaptive_depth_modifier(link_to_object, scene, layer_id):
	if bpy.data.node_groups.get("AdaptiveDepth") is None:
		blend_file_path = f"{get_addon_directory()}/assets/psd_layer_lib.blend"

		if os.path.isfile(blend_file_path):
			with bpy.data.libraries.load(blend_file_path) as (data_from, data_to):
				data_to.node_groups = ["AdaptiveDepth"]

	if bpy.data.node_groups.get("AdaptiveDepth") is not None:
		if link_to_object.modifiers.get("AdaptiveDepth") is None:
			modifier = link_to_object.modifiers.new(name="AdaptiveDepth", type="NODES")
			modifier.node_group = bpy.data.node_groups["AdaptiveDepth"]

			modifier.properties.inputs.Socket_3.value = scene.camap_camera

			driver = link_to_object.driver_add('modifiers["AdaptiveDepth"].properties.inputs.Socket_6.value').driver
			driver.type = "AVERAGE"

			if driver.variables.get("layer_distance") is None:
				var = driver.variables.new()
				var.name = "layer_distance"
				var.targets[0].id_type = "SCENE"
				var.targets[0].id = scene
				var.targets[0].data_path = f"camap_layers[{layer_id}].layer_distance"

			driver = link_to_object.driver_add('modifiers["AdaptiveDepth"].properties.inputs.Socket_10.value', 0).driver
			driver.type = "AVERAGE"

			if driver.variables.get("cam_distance_min") is None:
				var = driver.variables.new()
				var.name = "cam_distance_min"
				var.targets[0].id_type = "SCENE"
				var.targets[0].id = scene
				var.targets[0].data_path = f"camap_cam_distance_min"

			driver = link_to_object.driver_add('modifiers["AdaptiveDepth"].properties.inputs.Socket_10.value', 1).driver
			driver.type = "AVERAGE"

			if driver.variables.get("cam_distance_max") is None:
				var = driver.variables.new()
				var.name = "cam_distance_max"
				var.targets[0].id_type = "SCENE"
				var.targets[0].id = scene
				var.targets[0].data_path = f"camap_cam_distance_max"

			driver = link_to_object.driver_add('modifiers["AdaptiveDepth"].properties.inputs.Socket_2.value').driver
			driver.type = "AVERAGE"

			if driver.variables.get("floor_offset") is None:
				var = driver.variables.new()
				var.name = "floor_offset"
				var.targets[0].id_type = "SCENE"
				var.targets[0].id = scene
				var.targets[0].data_path = f"camap_layers[{layer_id}].floor_offset"

			driver = link_to_object.driver_add('modifiers["AdaptiveDepth"].properties.inputs.Socket_5.value').driver
			driver.type = "AVERAGE"

			if driver.variables.get("floor") is None:
				var = driver.variables.new()
				var.name = "floor"
				var.targets[0].id_type = "SCENE"
				var.targets[0].id = scene
				var.targets[0].data_path = f"camap_layers[{layer_id}].floor"


def create_camera_projection_modifier(link_to_object, scene, image_file, layer):
	if bpy.data.node_groups.get("CameraProjection") is None:
		blend_file_path = f"{get_addon_directory()}/assets/psd_layer_lib.blend"

		if os.path.isfile(blend_file_path):
			with bpy.data.libraries.load(blend_file_path) as (data_from, data_to):
				data_to.node_groups = ["CameraProjection"]

	if bpy.data.node_groups.get("CameraProjection") is not None:
		if link_to_object.modifiers.get("CameraProjection") is None:
			modifier = link_to_object.modifiers.new(name="CameraProjection", type="NODES")
			modifier.node_group = bpy.data.node_groups["CameraProjection"]

			modifier.properties.inputs.Socket_2.value = scene.camap_camera

			layer_missing = layer is not None
			image_file_missing = image_file is not None

			if layer_missing or image_file_missing:
				modifier.properties.inputs.Socket_3.value[0] = layer.offset[0] / image_file.size[0] * -1
				modifier.properties.inputs.Socket_3.value[1] = -1 + layer.size[1] / image_file.size[1] + layer.offset[1] / image_file.size[1]
				modifier.properties.inputs.Socket_4.value[0] = image_file.size[0] / layer.size[0]
				modifier.properties.inputs.Socket_4.value[1] = image_file.size[1] / layer.size[1]


def create_material(link_to_object, camera_obj, layer_name, layer_opacity, image_texture):
	material_name = f"material_{layer_name}"

	material = bpy.data.materials.get(material_name)

	if material is None:
		material = bpy.data.materials.new(material_name)
		material.use_nodes = True

		node_tree = material.node_tree
		nodes = node_tree.nodes
		nodes.remove(nodes["Principled BSDF"])
		emission_node = nodes.new("ShaderNodeEmission")
		image_node = nodes.new("ShaderNodeTexImage")
		transp_node = nodes.new("ShaderNodeBsdfTransparent")
		opacity_node = nodes.new("ShaderNodeMath")
		mix_node = nodes.new("ShaderNodeMixShader")
		out_node = nodes["Material Output"]

		emission_node.location = (-200, 140)
		image_node.location = (-540, 300)
		image_node.extension = "CLIP"
		transp_node.location = (-200, 260)
		opacity_node.location = (-200, 440)
		opacity_node.label = "Opacity"
		opacity_node.operation = "MULTIPLY"
		opacity_node.inputs[1].default_value = layer_opacity
		mix_node.location = (40, 300)
		out_node.location = (220, 300)

		image_node.image = image_texture

		node_tree.links.new(image_node.outputs["Color"], emission_node.inputs["Color"])
		node_tree.links.new(image_node.outputs["Alpha"], opacity_node.inputs[0])
		node_tree.links.new(opacity_node.outputs["Value"], mix_node.inputs["Fac"])
		node_tree.links.new(transp_node.outputs["BSDF"], mix_node.inputs[1])
		node_tree.links.new(emission_node.outputs["Emission"], mix_node.inputs[2])
		node_tree.links.new(mix_node.outputs["Shader"], out_node.inputs["Surface"])

		tex_coord_node = nodes.new("ShaderNodeTexCoord")
		tex_coord_node.object = camera_obj

		tex_coord_node.location = (-740, 300)
		node_tree.links.new(tex_coord_node.outputs["UV"], image_node.inputs["Vector"])

		link_to_object.data.materials.append(material)


def load_psd_layer(scene, image_file, layer, layer_id, index):
	array = layer.numpy()

	if np.shape(array)[2] == 3:
		alpha_channel = np.ones((np.shape(array)[0], np.shape(array)[1], 1), dtype=array.dtype)
		array = np.dstack((array, alpha_channel))

	if image_file.color_mode == psd_tools.constants.ColorMode.CMYK:
		# Convert CMYK to RGB
		C, M, Y, K, A = array[:, :, 0], array[:, :, 1], array[:, :, 2], array[:, :, 3], array[:, :, 4]

		R = C * K
		G = M * K
		B = Y * K

		array = np.stack((R, G, B, A), axis=-1).astype(np.float32)

	image_name = f"image_{layer.name}"
	image_texture = bpy.data.images.get(image_name)
	if image_texture is None:
		image_texture = bpy.data.images.new(name=image_name, width=layer.size[0], height=layer.size[1], alpha=True)

	img_size = image_texture.size[:]
	array_size = np.shape(array)[:2][::-1]

	if img_size != array_size:
		image_texture.scale(*array_size)

	array = np.flipud(array)

	image_texture.pixels.foreach_set(array.flatten())

	bbox = [
		layer.offset[0] / image_file.size[0],
		layer.offset[1] / image_file.size[1],
		(layer.offset[0] + layer.size[0]) / image_file.size[0],
		(layer.offset[1] + layer.size[1]) / image_file.size[1]
	]

	plane_obj = create_plane(name=layer.name, bbox=bbox, camera=scene.camap_camera, distance=(scene.camap_layer_count - index) * 2)

	create_adaptive_depth_modifier(link_to_object=plane_obj, scene=scene, layer_id=layer_id)
	create_camera_projection_modifier(link_to_object=plane_obj, scene=scene, image_file=image_file, layer=layer)

	create_material(
		link_to_object=plane_obj,
		camera_obj=scene.camap_camera,
		layer_name=layer.name,
		layer_opacity=layer.opacity / 255,
		image_texture=image_texture
	)

	for camap_layer in scene.camap_layers:
		if camap_layer.layer_id == layer_id:
			camap_layer.layer_object = plane_obj


def get_addon_directory():
	path = os.path.dirname(os.path.realpath(__file__))
	return bpy.path.abspath(path)


class CAMAP_PG_FileItem(PropertyGroup):
	file_name: bpy.props.StringProperty(name="File Name", description="File name")
	image_path: bpy.props.StringProperty(name="Image Path", subtype="FILE_PATH", description="Path to input image", default="", update=load_new_camap_file)


class CAMAP_PG_LayerItem(PropertyGroup):
	layer_name: bpy.props.StringProperty(name="layer", description="Layer name")
	file: bpy.props.PointerProperty(type=CAMAP_PG_FileItem, name="file", description="File property group")
	layer_id: bpy.props.IntProperty(name="layer_id", description="Layer index")
	psd_layer_id: bpy.props.IntProperty(name="psd_layer_id", description="PSD layer index")
	layer_object: bpy.props.PointerProperty(name="layer_object", description="Layer object", type=bpy.types.Object)
	layer_distance: bpy.props.FloatProperty(name="layer_distance", description="Layer distance from camera", subtype="FACTOR", default=1.0, min=0.0, max=1.0)
	floor: bpy.props.BoolProperty(name="floor", description="Layer floored")
	floor_offset: bpy.props.FloatProperty(name="floor_offset", description="Layer offset when floored", default=0.0)


class CAMAP_UL_FilesList(UIList):
	def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index, flt_flag):
		# custom_icon = "OBJECT_DATAMODE"
		if self.layout_type in {"DEFAULT", "COMPACT"}:
			row = layout.row(align=True)
			row.prop(item, "file_name", text="", emboss=False, icon="IMAGE_DATA")

		elif self.layout_type == "GRID":
			layout.alignment = "CENTER"
			layout.label(text=item.layer_name)


class CAMAP_UL_LayersList(UIList):
	def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index, flt_flag):
		# custom_icon = "OBJECT_DATAMODE"
		if self.layout_type in {"DEFAULT", "COMPACT"}:
			scene = context.scene

			row = layout.row(align=True)
			camap_files_bool = len(scene.camap_files) > 0
			if camap_files_bool:
				camap_files_bool = scene.camap_files[scene.camap_file_index] == item.file
			row.label(text=item.layer_name, icon="DOT" if camap_files_bool else "BLANK1")

			row = layout.row(align=True)
			if item.floor:
				prop_offset = row.prop(item, "floor_offset", text="")
			else:
				prop_distance = row.prop(item, "layer_distance", text="")

			row = layout.row(align=True)
			op_select = row.operator("camap.select_layer_object", icon="RESTRICT_SELECT_OFF" if item.layer_object == context.object else "RESTRICT_SELECT_ON", emboss=False)
			op_select.item_index = index
			row.enabled = select_plane_poll(context, item)
			row = layout.row(align=True)
			op_floor = row.operator("camap.toggle_floor", icon="VIEW_PERSPECTIVE" if item.floor else "VIEW_ORTHO", emboss=False)
			op_floor.item_index = index

			row = layout.row(align=True)
			op_load_layer = row.operator("camap.load_layer", icon="FILE_REFRESH", emboss=False)
			op_load_layer.item_index = index
			row.enabled = create_planes_poll(context)

		elif self.layout_type == "GRID":
			layout.alignment = "CENTER"
			layout.label(text=item.layer_name)


class CAMAP_PT_FilesPanel(Panel):
	"""Creates a Panel for image files handling"""
	bl_category = "Camap"
	bl_label = "Camap Files"
	bl_idname = "OBJECT_PT_camap_files"
	bl_space_type = "VIEW_3D"
	bl_region_type = "UI"

	def draw(self, context):
		scene = context.scene
		layout = self.layout

		row = layout.row()
		row.template_list(
			listtype_name="CAMAP_UL_FilesList",
			list_id="",
			dataptr=scene,
			propname="camap_files",
			active_dataptr=scene,
			active_propname="camap_file_index",
			sort_reverse=True,
			sort_lock=True
		)

		col = row.column(align=True)

		col.operator("camap.file_add", icon="ADD", text="")
		col.operator("camap.file_remove", icon="REMOVE", text="")

		if len(scene.camap_files) > 0:
			row = layout.row()
			selected_file = scene.camap_files[scene.camap_file_index]
			prop_image_path = row.prop(selected_file, "image_path")

		# row = layout.row()
		# row.prop(context.scene, "camap_file")


class CAMAP_PT_LayersPanel(Panel):
	"""Creates a Panel for layers handling"""
	bl_category = "Camap"
	bl_label = "Camap Layers"
	bl_idname = "OBJECT_PT_camap_layers"
	bl_space_type = "VIEW_3D"
	bl_region_type = "UI"

	def draw(self, context):
		scene = context.scene
		layout = self.layout

		row = layout.row()
		row.prop(context.scene, "camap_camera")

		row = layout.row(align=True)
		row.use_property_decorate = False
		row.prop(context.scene, "camap_cam_distance_min")
		row.prop(context.scene, "camap_cam_distance_max")

		row = layout.row()
		row.template_list(
			listtype_name="CAMAP_UL_LayersList",
			list_id="",
			dataptr=scene,
			propname="camap_layers",
			active_dataptr=scene,
			active_propname="camap_layer_index",
			sort_reverse=True,
			sort_lock=True
		)

		if len(scene.camap_layers) > 0:
			row = layout.row()
			selected_layer = scene.camap_layers[scene.camap_layer_index]
			if selected_layer.floor:
				prop_offset = row.prop(selected_layer, "floor_offset", text="Floor Offset")
			else:
				prop_distance = row.prop(selected_layer, "layer_distance", text="Layer Distance")

		row = layout.row()
		row.operator("camap.load_camap_layers")

		row = layout.row()
		row.operator("camap.apply_position")


class CAMAP_OT_FileAdd(Operator):
	bl_idname = "camap.file_add"
	bl_label = ""
	bl_description = "Add image file"

	def execute(self, context):
		scene = context.scene
		item = scene.camap_files.add()
		item.file_name = "New File"

		scene.camap_file_index = len(scene.camap_files) - 1
		
		return {"FINISHED"}


class CAMAP_OT_FileRemove(Operator):
	bl_idname = "camap.file_remove"
	bl_label = ""
	bl_description = "Remove image file"

	@classmethod
	def poll(cls, context):
		return len(context.scene.camap_files) > 0

	def execute(self, context):
		scene = context.scene
		selected_file = scene.camap_files[scene.camap_file_index]
		remove_file_layers(selected_file)
		scene.camap_files.remove(scene.camap_file_index)

		scene.camap_file_index = max(0, scene.camap_file_index - 1)

		return {"FINISHED"}


class CAMAP_OT_LoadPSDLayer(Operator):
	bl_idname = "camap.load_layer"
	bl_label = ""
	bl_description = "Creates/Updates layer object"

	item_index: bpy.props.IntProperty()

	def execute(self, context):
		scene = context.scene
		selected_layer_id = scene.camap_layers[self.item_index].layer_id
		file_id = scene.camap_layers[self.item_index].file_id

		psd = psd_tools.PSDImage.open(scene.camap_files[file_id].image_path)

		scene.render.resolution_x = psd.size[0]
		scene.render.resolution_y = psd.size[1]

		i = 0
		for layer_id, layer in enumerate(psd.descendants()):
			if layer.kind == "pixel":
				if layer_id == selected_layer_id:
					load_psd_layer(scene=scene, image_file=psd, layer=layer, layer_id=layer_id, index=i)
					break

				i += 1

		return {"FINISHED"}


class CAMAP_OT_ToggleFloor(Operator):
	bl_idname = "camap.toggle_floor"
	bl_label = ""
	bl_description = "Toggle floor for layer"

	item_index: bpy.props.IntProperty()

	def execute(self, context):
		scene = context.scene
		layer = scene.camap_layers[self.item_index]

		# if layer_object is not None:
		# 	layer_object.modifiers["AdaptiveDepth"]["Socket_5"] = not layer_object.modifiers["AdaptiveDepth"]["Socket_5"]

		layer.floor = not layer.floor

		bpy.ops.camap.load_layer(item_index=self.item_index)

		return {"FINISHED"}


class CAMAP_OT_SelectLayerObject(Operator):
	bl_idname = "camap.select_layer_object"
	bl_label = ""
	bl_description = "Select layer object"

	item_index: bpy.props.IntProperty()

	def execute(self, context):
		scene = context.scene
		layer_object = scene.camap_layers[self.item_index].layer_object
		if layer_object is not None:
			if context.object is not None:
				if context.object.mode == "OBJECT":
					bpy.ops.object.select_all(action="DESELECT")
					layer_object.select_set(True)
					context.view_layer.objects.active = layer_object
			else:
				context.view_layer.objects.active = layer_object

		return {"FINISHED"}


class CAMAP_OT_LoadCamapLayers(Operator):
	bl_idname = "camap.load_camap_layers"
	bl_label = "Load all layers"
	bl_description = "Creates all layer objects"

	item_index: bpy.props.IntProperty()

	@classmethod
	def poll(cls, context):
		return load_layers_poll(context)

	def execute(self, context):
		scene = context.scene

		# for camap_file in scene.camap_files.values():
		# file_id = scene.camap_layers[self.item_index].file_id
		selected_image_path = Path(camap_file.image_path)
		layer_items_list = get_layers_from_file(camap_file.file_id)

		if selected_image_path.suffix.lower() == ".psd":
			psd = psd_tools.PSDImage.open(selected_image_path)

			scene.render.resolution_x = psd.size[0]
			scene.render.resolution_y = psd.size[1]

			descendants_list = list(psd.descendants())

			i = 0
			for layer_item in layer_items_list:
				layer = descendants_list[layer_item.psd_layer_id]

				if layer is None:
					continue

				if layer.kind == "pixel":
					load_psd_layer(scene=scene, image_file=psd, layer=layer, layer_id=layer_item.layer_id, index=i)
					i += 1

			# for layer_id, layer in enumerate(psd.descendants()):
			# 	if layer.kind == "pixel":
			# 		load_psd_layer(scene=scene, image_file=psd, layer=layer, layer_id=layer_id, index=i)


		elif selected_image_path.suffix.lower() in (".png", ".jpg", ".jpeg"):
			if layer_items_list:
				layer_item = layer_items_list[0]

				image_texture = bpy.data.images.load(selected_image_path.as_posix(), check_existing=True)

				bbox = [0, 0, 1, 1]
				plane_obj = create_plane(name=layer_item.layer_name, bbox=bbox, camera=scene.camap_camera, distance=0.5)

				create_adaptive_depth_modifier(link_to_object=plane_obj, scene=scene, layer_id=layer_item.layer_id)
				create_camera_projection_modifier(link_to_object=plane_obj, scene=scene, image_file=None, layer=None)

				create_material(
					link_to_object=plane_obj,
					camera_obj=scene.camap_camera,
					layer_name=layer_item.layer_name,
					layer_opacity=1.0,
					image_texture=image_texture
				)


		return {"FINISHED"}


class CAMAP_OT_ApplyPosition(Operator):
	bl_idname = "camap.apply_position"
	bl_label = "Apply position"
	bl_description = "Apply position from geometry node modifier"

	@classmethod
	def poll(cls, context):
		return create_planes_poll(context)

	def execute(self, context):
		scene = context.scene

		selected_layer = context.scene.camap_layers[context.scene.camap_layer_index]
		distance = selected_layer.layer_distance
		layer_object = selected_layer.layer_object

		depth_mod = layer_object.modifiers.get("AdaptiveDepth")
		if depth_mod is not None:
			apply_modifier(target_object=layer_object, modifier=depth_mod)

		selected_layer.layer_distance = distance
		create_adaptive_depth_modifier(link_to_object=layer_object, scene=scene, layer_id=context.scene.camap_layer_index)

		return {"FINISHED"}


classes = (
	CAMAP_UL_FilesList,
	CAMAP_UL_LayersList,
	CAMAP_PG_FileItem,
	CAMAP_PG_LayerItem,
	CAMAP_OT_FileAdd,
	CAMAP_OT_FileRemove,
	CAMAP_OT_LoadPSDLayer,
	CAMAP_OT_ToggleFloor,
	CAMAP_OT_SelectLayerObject,
	CAMAP_OT_LoadCamapLayers,
	CAMAP_OT_ApplyPosition,
	CAMAP_PT_FilesPanel,
	CAMAP_PT_LayersPanel
)


def register():
	from bpy.utils import register_class

	for cls in classes:
		register_class(cls)

	bpy.types.Scene.camap_files = CollectionProperty(type=CAMAP_PG_FileItem)
	bpy.types.Scene.camap_file_index = IntProperty(name="File Index", description="File index", default=0)

	bpy.types.Scene.camap_layers = CollectionProperty(type=CAMAP_PG_LayerItem)
	bpy.types.Scene.camap_layer_count = IntProperty(name="PSD Layer Count", description="Number of layer in PSD file", default=0)
	bpy.types.Scene.camap_layer_index = IntProperty(name="Layer Index", description="Layer index", default=0)
	bpy.types.Scene.camap_camera = PointerProperty(name="Projection Camera", description="Camera from which to project", type=bpy.types.Object, poll=camera_object_poll)
	bpy.types.Scene.camap_cam_distance_min = FloatProperty(name="Camera Distance Min", description="Camera distance min", subtype="DISTANCE", default=3.0)
	bpy.types.Scene.camap_cam_distance_max = FloatProperty(name="Camera Distance Max", description="Camera distance max", subtype="DISTANCE", default=10.0)


def unregister():
	from bpy.utils import unregister_class

	del bpy.types.Scene.camap_cam_distance_max
	del bpy.types.Scene.camap_cam_distance_min
	del bpy.types.Scene.camap_camera
	del bpy.types.Scene.camap_layer_index
	del bpy.types.Scene.camap_layer_count
	del bpy.types.Scene.camap_layers
	del bpy.types.Scene.camap_file_index
	del bpy.types.Scene.camap_files

	for cls in reversed(classes):
		unregister_class(cls)


if __name__ == "__main__":
	register()
