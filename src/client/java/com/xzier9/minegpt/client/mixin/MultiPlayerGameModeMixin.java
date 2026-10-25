package com.xzier9.minegpt.client.mixin;

import com.xzier9.minegpt.client.MineGPTClient;
import net.minecraft.client.multiplayer.MultiPlayerGameMode;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.inventory.ContainerInput;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

/** Lets the recorder see inventory clicks, which have no key binding to read. */
@Mixin(MultiPlayerGameMode.class)
public class MultiPlayerGameModeMixin {
	@Inject(at = @At("HEAD"), method = "handleContainerInput")
	private void minegpt$recordContainerInput(int containerId, int slot, int button, ContainerInput input, Player player, CallbackInfo info) {
		MineGPTClient.recorder().onContainerInput(containerId, slot, button, input);
	}
}
