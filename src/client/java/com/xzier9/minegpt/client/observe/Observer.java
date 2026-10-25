package com.xzier9.minegpt.client.observe;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.client.multiplayer.ClientLevel;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.world.effect.MobEffectInstance;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.player.Inventory;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.inventory.AbstractContainerMenu;
import net.minecraft.world.inventory.InventoryMenu;
import net.minecraft.world.inventory.Slot;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.EntityHitResult;
import net.minecraft.world.phys.HitResult;
import net.minecraft.world.phys.Vec3;

/**
 * Turns what the local player can see into a JSON observation.
 *
 * Everything here reads client-side state only, so it works the same in singleplayer
 * and on multiplayer servers. The format is described in docs/protocol.md.
 */
public final class Observer {
	public static final int SCHEMA_VERSION = 1;

	/** Horizontal radius of the block grid around the player's feet. */
	public static final int GRID_RADIUS = 8;
	public static final int GRID_BELOW = 4;
	public static final int GRID_ABOVE = 4;

	public static final double ENTITY_RANGE = 24.0;
	public static final int MAX_ENTITIES = 32;

	private Observer() {
	}

	/** Returns null when there is no world loaded. */
	public static JsonObject observe(Minecraft mc, boolean includeGrid) {
		LocalPlayer player = mc.player;
		ClientLevel level = mc.level;
		if (player == null || level == null) {
			return null;
		}

		JsonObject obs = new JsonObject();
		obs.addProperty("schema", SCHEMA_VERSION);
		obs.addProperty("gameTime", level.getLevelData().getGameTime());
		obs.addProperty("dayTime", level.getDefaultClockTime());
		obs.addProperty("dimension", level.dimension().identifier().toString());
		obs.addProperty("biome", level.getBiome(player.blockPosition()).getRegisteredName());
		obs.addProperty("raining", level.isRaining());
		obs.addProperty("thundering", level.isThundering());
		obs.addProperty("multiplayer", !mc.hasSingleplayerServer());

		obs.add("player", playerState(player));
		obs.add("inventory", inventory(player.getInventory()));
		obs.add("target", target(mc, level));
		obs.add("entities", entities(player, level));
		obs.add("screen", screen(mc, player));
		if (includeGrid) {
			obs.add("grid", grid(player, level));
		}
		return obs;
	}

	private static JsonObject playerState(LocalPlayer player) {
		JsonObject p = new JsonObject();
		p.add("pos", vec(player.position()));
		p.addProperty("yaw", player.getYRot());
		p.addProperty("pitch", player.getXRot());
		p.add("velocity", vec(player.getDeltaMovement()));
		p.addProperty("onGround", player.onGround());
		p.addProperty("inWater", player.isInWater());
		p.addProperty("onFire", player.isOnFire());
		p.addProperty("health", player.getHealth());
		p.addProperty("maxHealth", player.getMaxHealth());
		p.addProperty("armor", player.getArmorValue());
		p.addProperty("food", player.getFoodData().getFoodLevel());
		p.addProperty("saturation", player.getFoodData().getSaturationLevel());
		p.addProperty("air", player.getAirSupply());
		p.addProperty("maxAir", player.getMaxAirSupply());
		p.addProperty("xpLevel", player.experienceLevel);
		p.addProperty("xpProgress", player.experienceProgress);
		p.addProperty("creative", player.isCreative());
		p.addProperty("usingItem", player.isUsingItem());

		JsonArray effects = new JsonArray();
		for (MobEffectInstance effect : player.getActiveEffects()) {
			JsonObject e = new JsonObject();
			e.addProperty("id", effect.getEffect().getRegisteredName());
			e.addProperty("amplifier", effect.getAmplifier());
			e.addProperty("duration", effect.getDuration());
			effects.add(e);
		}
		p.add("effects", effects);
		return p;
	}

	private static JsonObject inventory(Inventory inv) {
		JsonObject out = new JsonObject();
		out.addProperty("selected", inv.getSelectedSlot());
		JsonArray slots = new JsonArray();
		for (int i = 0; i < inv.getContainerSize(); i++) {
			ItemStack stack = inv.getItem(i);
			if (!stack.isEmpty()) {
				JsonObject s = item(stack);
				s.addProperty("slot", i);
				slots.add(s);
			}
		}
		out.add("slots", slots);
		return out;
	}

	private static JsonObject target(Minecraft mc, ClientLevel level) {
		JsonObject t = new JsonObject();
		HitResult hit = mc.hitResult;
		if (hit == null || hit.getType() == HitResult.Type.MISS) {
			t.addProperty("type", "none");
		} else if (hit instanceof BlockHitResult blockHit) {
			BlockPos pos = blockHit.getBlockPos();
			t.addProperty("type", "block");
			t.add("pos", blockPos(pos));
			t.addProperty("face", blockHit.getDirection().getSerializedName());
			t.addProperty("block", blockId(level.getBlockState(pos)));
		} else if (hit instanceof EntityHitResult entityHit) {
			t.addProperty("type", "entity");
			t.addProperty("entityId", entityHit.getEntity().getId());
			t.addProperty("entity", BuiltInRegistries.ENTITY_TYPE.getKey(entityHit.getEntity().getType()).toString());
		}
		return t;
	}

	private static JsonArray entities(LocalPlayer player, ClientLevel level) {
		AABB box = player.getBoundingBox().inflate(ENTITY_RANGE);
		List<Entity> nearby = level.getEntities(player, box, e -> e.distanceTo(player) <= ENTITY_RANGE);
		nearby.sort(Comparator.comparingDouble(e -> e.distanceTo(player)));

		JsonArray out = new JsonArray();
		for (Entity entity : nearby.subList(0, Math.min(nearby.size(), MAX_ENTITIES))) {
			JsonObject e = new JsonObject();
			e.addProperty("id", entity.getId());
			e.addProperty("type", BuiltInRegistries.ENTITY_TYPE.getKey(entity.getType()).toString());
			e.add("pos", vec(entity.position()));
			e.addProperty("distance", entity.distanceTo(player));
			if (entity instanceof LivingEntity living) {
				e.addProperty("health", living.getHealth());
			}
			if (entity instanceof Player other) {
				e.addProperty("name", other.getName().getString());
			}
			out.add(e);
		}
		return out;
	}

	private static JsonObject screen(Minecraft mc, LocalPlayer player) {
		JsonObject s = new JsonObject();
		Screen open = mc.gui.screen();
		s.addProperty("open", open != null);
		if (open != null) {
			s.addProperty("class", open.getClass().getSimpleName());
		}

		AbstractContainerMenu menu = player.containerMenu;
		// The player's own inventory menu is always "open" in the background; only report other menus.
		if (menu != null && !(menu instanceof InventoryMenu)) {
			JsonObject m = new JsonObject();
			m.addProperty("containerId", menu.containerId);
			m.addProperty("type", menuType(menu));
			JsonArray slots = new JsonArray();
			for (Slot slot : menu.slots) {
				ItemStack stack = slot.getItem();
				if (!stack.isEmpty()) {
					JsonObject j = item(stack);
					j.addProperty("slot", slot.index);
					j.addProperty("playerInventory", slot.container instanceof Inventory);
					slots.add(j);
				}
			}
			m.add("slots", slots);
			if (!menu.getCarried().isEmpty()) {
				m.add("carried", item(menu.getCarried()));
			}
			s.add("menu", m);
		}
		return s;
	}

	/**
	 * Block ids in a cube around the player's feet, as a palette plus a flat index array.
	 * Index order is y, then z, then x (x varies fastest). -1 means the chunk is not loaded.
	 */
	private static JsonObject grid(LocalPlayer player, ClientLevel level) {
		BlockPos origin = player.blockPosition();
		Map<String, Integer> palette = new HashMap<>();
		JsonArray paletteJson = new JsonArray();
		JsonArray cells = new JsonArray();
		BlockPos.MutableBlockPos pos = new BlockPos.MutableBlockPos();

		for (int dy = -GRID_BELOW; dy <= GRID_ABOVE; dy++) {
			for (int dz = -GRID_RADIUS; dz <= GRID_RADIUS; dz++) {
				for (int dx = -GRID_RADIUS; dx <= GRID_RADIUS; dx++) {
					pos.set(origin.getX() + dx, origin.getY() + dy, origin.getZ() + dz);
					if (!level.isLoaded(pos)) {
						cells.add(-1);
						continue;
					}
					String id = blockId(level.getBlockState(pos));
					Integer index = palette.get(id);
					if (index == null) {
						index = palette.size();
						palette.put(id, index);
						paletteJson.add(id);
					}
					cells.add(index);
				}
			}
		}

		JsonObject g = new JsonObject();
		g.add("origin", blockPos(origin));
		g.addProperty("radius", GRID_RADIUS);
		g.addProperty("below", GRID_BELOW);
		g.addProperty("above", GRID_ABOVE);
		g.add("palette", paletteJson);
		g.add("cells", cells);
		return g;
	}

	private static String menuType(AbstractContainerMenu menu) {
		try {
			return String.valueOf(BuiltInRegistries.MENU.getKey(menu.getType()));
		} catch (UnsupportedOperationException e) {
			// Menus without a registered type (such as the creative inventory) throw here.
			return "unknown";
		}
	}

	public static JsonObject item(ItemStack stack) {
		JsonObject s = new JsonObject();
		s.addProperty("id", BuiltInRegistries.ITEM.getKey(stack.getItem()).toString());
		s.addProperty("count", stack.getCount());
		if (stack.getMaxDamage() > 0) {
			s.addProperty("damage", stack.getDamageValue());
			s.addProperty("maxDamage", stack.getMaxDamage());
		}
		return s;
	}

	private static String blockId(BlockState state) {
		return BuiltInRegistries.BLOCK.getKey(state.getBlock()).toString();
	}

	private static JsonArray vec(Vec3 v) {
		JsonArray a = new JsonArray();
		a.add(v.x);
		a.add(v.y);
		a.add(v.z);
		return a;
	}

	private static JsonArray blockPos(BlockPos p) {
		JsonArray a = new JsonArray();
		a.add(p.getX());
		a.add(p.getY());
		a.add(p.getZ());
		return a;
	}
}
