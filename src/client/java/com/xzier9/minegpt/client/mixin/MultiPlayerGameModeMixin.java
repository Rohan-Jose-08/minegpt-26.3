package com.xzier9.minegpt.client.mixin;

import com.xzier9.minegpt.client.MineGPTClient;
import com.xzier9.minegpt.client.safety.Guard;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.client.multiplayer.MultiPlayerGameMode;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.inventory.ContainerInput;
import net.minecraft.world.phys.BlockHitResult;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/**
 * Lets the recorder see inventory clicks, which have no key binding to read, and puts the
 * safety rails ({@link Guard}) in front of every block break, block use and attack, so they
 * hold whichever skill or raw action is pressing the keys.
 */
@Mixin(MultiPlayerGameMode.class)
public class MultiPlayerGameModeMixin {
	@Inject(at = @At("HEAD"), method = "handleContainerInput")
	private void minegpt$recordContainerInput(int containerId, int slot, int button, ContainerInput input, Player player, CallbackInfo info) {
		MineGPTClient.recorder().onContainerInput(containerId, slot, button, input);
	}

	@Inject(at = @At("HEAD"), method = "startDestroyBlock", cancellable = true)
	private void minegpt$guardStartDestroy(BlockPos pos, Direction face, CallbackInfoReturnable<Boolean> info) {
		if (!Guard.mayBreak(pos)) {
			info.setReturnValue(false);
		}
	}

	@Inject(at = @At("HEAD"), method = "continueDestroyBlock", cancellable = true)
	private void minegpt$guardContinueDestroy(BlockPos pos, Direction face, CallbackInfoReturnable<Boolean> info) {
		if (!Guard.mayBreak(pos)) {
			info.setReturnValue(false);
		}
	}

	@Inject(at = @At("HEAD"), method = "destroyBlock")
	private void minegpt$countDestroy(BlockPos pos, CallbackInfoReturnable<Boolean> info) {
		Guard.count();
	}

	@Inject(at = @At("HEAD"), method = "useItemOn", cancellable = true)
	private void minegpt$guardUseItemOn(LocalPlayer player, InteractionHand hand, BlockHitResult hit,
			CallbackInfoReturnable<InteractionResult> info) {
		if (!Guard.mayUseOn(player, hand, hit)) {
			info.setReturnValue(InteractionResult.FAIL);
		}
	}

	@Inject(at = @At("HEAD"), method = "attack")
	private void minegpt$countAttack(Player player, Entity target, CallbackInfo info) {
		Guard.count();
	}
}
