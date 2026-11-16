package com.xzier9.minegpt.client.skill;

import com.google.gson.JsonObject;
import com.xzier9.minegpt.client.act.Action.MoveKey;
import com.xzier9.minegpt.path.Blacklist;
import com.xzier9.minegpt.path.Commitment;
import com.xzier9.minegpt.path.Goal;
import com.xzier9.minegpt.path.StuckWatch;
import java.util.Comparator;
import java.util.EnumSet;
import java.util.Set;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.resources.Identifier;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.monster.Creeper;
import net.minecraft.world.entity.monster.Enemy;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.phys.Vec3;

/**
 * Fights hostile mobs nearby with the best weapon carried, swinging only at full attack
 * strength, and stops when none are left or health runs low. When hunting a named kind, such
 * as cows, it picks up the drops afterwards.
 *
 * It stays on one mob until it is dead, and only turns to another when that one has come into
 * striking range while the first is still some way off. A mob there is no way to, or that the
 * player has stood pushing towards without getting any closer, is left alone for a while and
 * the next one is tried.
 */
public final class FightSkill extends Skill {
	/** Mobs left alone unless named: neutral until provoked, or not worth fighting. */
	private static final Set<String> LEAVE_ALONE = Set.of("minecraft:enderman", "minecraft:zombified_piglin",
			"minecraft:piglin", "minecraft:warden", "minecraft:wither", "minecraft:ender_dragon", "minecraft:creaking");
	/** Back off this long after hitting a creeper, so it does not explode next to the player. */
	private static final int CREEPER_BACKOFF = 12;
	/** A mob this close is a threat to deal with first. */
	private static final double THREAT_RANGE = 3.5;
	/** Ticks of walking at a mob without changing block, out of reach of it, before leaving it. */
	private static final int STILL_LIMIT = 60;
	/** How long a mob that could not be got to is left alone at first: 10 seconds, as mobs move. */
	private static final long OUT_OF_REACH_TICKS = 200;
	/** Mobs (entity ids) not worth going for again just yet. */
	static final Blacklist<Integer> OUT_OF_REACH = new Blacklist<>(OUT_OF_REACH_TICKS);

	private final Identifier type;
	private final double radius;
	private final float minHealth;
	private final Commitment commitment = new Commitment(40, 4);
	private final StuckWatch watch = new StuckWatch(STILL_LIMIT, Integer.MAX_VALUE, 2);
	private LivingEntity target;
	private int unreachable;
	private boolean hitTarget;
	private int kills;
	private int backOff;
	private boolean weaponChosen;
	private Vec3 lastKill;
	private boolean swept;
	private boolean sweeping;
	private String pickedUp;

	public FightSkill(Identifier type, double radius, float minHealth, int maxTicks) {
		super("fight", maxTicks);
		this.type = type;
		this.radius = radius;
		this.minHealth = minHealth;
	}

	static FightSkill fromArgs(Args args) {
		Identifier type = null;
		if (args.has("entity")) {
			type = Identifier.tryParse(args.string("entity"));
			if (type == null || !BuiltInRegistries.ENTITY_TYPE.containsKey(type)) {
				throw args.bad("unknown entity \"" + args.string("entity") + "\"");
			}
		}
		return new FightSkill(type, Math.max(2, Math.min(32, args.decimal("radius", 16))),
				(float) args.decimal("minHealth", 6), args.ticks("timeout", 60));
	}

	@Override
	protected SkillResult update(SkillContext ctx) {
		SkillResult child = childResult();
		long now = ctx.level.getGameTime();
		if (sweeping && child != null) {
			sweeping = false;
			pickedUp = child.message();
		} else if (child != null && !child.success() && !child.message().contains("timed out") && target != null
				&& target.isAlive() && !ctx.player.isWithinEntityInteractionRange(target, 0)) {
			// No way to it: leave it for now, rather than walking into the same wall again.
			leave(now);
		}
		if (ctx.player.getHealth() <= minHealth) {
			return SkillResult.failure("health is down to " + Math.round(ctx.player.getHealth()) + ", stopping the fight",
					data(ctx));
		}
		if (target != null && (!target.isAlive() || target.isRemoved())) {
			if (hitTarget) {
				kills++;
				lastKill = target.position();
			}
			target = null;
		}
		if (target != null && target.distanceTo(ctx.player) > radius * 1.5) {
			target = null;
		}
		if (target != null) {
			LivingEntity threat = moreUrgent(ctx, now);
			if (threat != null) {
				target = threat;
				hitTarget = false;
				backOff = 0;
				watch.reset();
				commitment.commit(now);
			}
		}
		if (target == null) {
			target = findTarget(ctx);
			hitTarget = false;
			backOff = 0;
			watch.reset();
			commitment.commit(now);
			if (target == null) {
				if (type != null && kills > 0 && !swept) {
					swept = true;
					sweeping = true;
					startChild(new CollectSkill(null, lastKill, 8, 10, 200));
					return null;
				}
				return SkillResult.success((kills == 0 ? "no enemies nearby" : "defeated " + kills + " enemies")
						+ (unreachable == 0 ? "" : "; " + unreachable + " out of reach")
						+ (pickedUp == null ? "" : "; " + pickedUp), data(ctx));
			}
		}
		if (!weaponChosen) {
			int slot = Inventories.bestWeaponSlot(ctx.player.getInventory());
			if (slot >= 0) {
				Inventories.select(ctx, slot);
			}
			weaponChosen = true;
		}

		double distance = ctx.player.distanceTo(target);
		if (!ctx.player.hasLineOfSight(target) || distance > 8) {
			startChild(GotoSkill.to(new Goal.Near(target.getBlockX(), target.getBlockY(), target.getBlockZ(), 2),
					"enemy", GotoSkill.Dig.NEVER, 4_000, 100));
			return null;
		}

		ctx.lookAt(target.getBoundingBox().getCenter());
		EnumSet<MoveKey> keys = EnumSet.noneOf(MoveKey.class);
		boolean inRange = ctx.player.isWithinEntityInteractionRange(target, 0);
		if (backOff > 0) {
			backOff--;
			keys.add(MoveKey.BACK);
		} else if (!inRange || distance > 2.5) {
			keys.add(MoveKey.FORWARD);
			if (distance > 4) {
				keys.add(MoveKey.SPRINT);
			}
			if (ctx.player.horizontalCollision && ctx.player.onGround()) {
				keys.add(MoveKey.JUMP);
			}
		}
		Footing.guard(ctx, keys);
		if (keys.contains(MoveKey.FORWARD) && !inRange) {
			BlockPos feet = ctx.feet();
			if (watch.tick(feet.getX(), feet.getY(), feet.getZ()) == StuckWatch.State.STILL) {
				// In sight but not getting any nearer: behind a fence, across water, up a ledge.
				leave(now);
				return null;
			}
		} else {
			watch.reset();
		}
		if (inRange && ctx.mc.crosshairPickEntity == target && ctx.player.getAttackStrengthScale(0.5F) >= 0.95F) {
			ctx.clickAttack();
			hitTarget = true;
			if (target instanceof Creeper) {
				backOff = CREEPER_BACKOFF;
			}
		}
		ctx.hold(keys);
		return null;
	}

	@Override
	protected boolean interruptChild(SkillContext ctx) {
		if (target == null || !target.isAlive()) {
			// Gone while walking to it.
			return target != null;
		}
		// Stop walking around once the target is in plain sight, or something else is on top of the player.
		return (ctx.player.hasLineOfSight(target) && ctx.player.distanceTo(target) <= 6)
				|| (ticks() % 5 == 0 && moreUrgent(ctx, ctx.level.getGameTime()) != null);
	}

	/** Leaves the target alone for a while. */
	private void leave(long now) {
		OUT_OF_REACH.add(target.getId(), now);
		unreachable++;
		target = null;
		watch.reset();
	}

	private int priority(SkillContext ctx, LivingEntity entity) {
		return entity.distanceTo(ctx.player) <= THREAT_RANGE && ctx.player.hasLineOfSight(entity) ? 1 : 0;
	}

	/**
	 * Another mob worth dropping the target for, or null: one in striking range while the target
	 * is not, or one much closer once the target has had a fair go. Hunting a named kind, where
	 * nothing fights back, only the second applies.
	 */
	private LivingEntity moreUrgent(SkillContext ctx, long now) {
		int current = priority(ctx, target);
		double left = target.distanceTo(ctx.player);
		if (current > 0 || hitTarget && left <= 6) {
			// Already in the thick of it with this one.
			return null;
		}
		LivingEntity best = null;
		double bestDistance = Double.MAX_VALUE;
		AABB box = ctx.player.getBoundingBox().inflate(radius);
		for (LivingEntity rival : ctx.level.getEntitiesOfClass(LivingEntity.class, box, this::isTarget)) {
			double distance = rival.distanceTo(ctx.player);
			if (rival == target || rival == ctx.player || distance >= bestDistance
					|| OUT_OF_REACH.contains(rival.getId(), now) || !ctx.player.hasLineOfSight(rival)) {
				continue;
			}
			if (commitment.shouldSwitch(now, left, current, distance, type == null ? priority(ctx, rival) : 0)) {
				best = rival;
				bestDistance = distance;
			}
		}
		return best;
	}

	@Override
	protected String describe() {
		return target == null ? "looking for enemies"
				: "fighting " + BuiltInRegistries.ENTITY_TYPE.getKey(target.getType()).getPath();
	}

	private LivingEntity findTarget(SkillContext ctx) {
		AABB box = ctx.player.getBoundingBox().inflate(radius);
		long now = ctx.level.getGameTime();
		return ctx.level.getEntitiesOfClass(LivingEntity.class, box, this::isTarget).stream()
				.filter(e -> e != ctx.player && e.distanceTo(ctx.player) <= radius
						// One that could not be got to is still fought if it comes within arm's reach.
						&& (!OUT_OF_REACH.contains(e.getId(), now) || e.distanceTo(ctx.player) <= THREAT_RANGE))
				// Mobs in sight first, then the nearest.
				.min(Comparator.<LivingEntity>comparingInt(e -> ctx.player.hasLineOfSight(e) ? 0 : 1)
						.thenComparingDouble(e -> e.distanceToSqr(ctx.player)))
				.orElse(null);
	}

	private boolean isTarget(LivingEntity entity) {
		if (!entity.isAlive()) {
			return false;
		}
		Identifier id = BuiltInRegistries.ENTITY_TYPE.getKey(entity.getType());
		if (type != null) {
			return id.equals(type);
		}
		return entity instanceof Enemy && !LEAVE_ALONE.contains(id.toString());
	}

	private JsonObject data(SkillContext ctx) {
		JsonObject data = new JsonObject();
		data.addProperty("kills", kills);
		data.addProperty("unreachable", unreachable);
		data.addProperty("health", ctx.player.getHealth());
		return data;
	}
}
