package com.xzier9.minegpt.client.mixin;

import com.llamalad7.mixinextras.injector.ModifyExpressionValue;
import com.xzier9.minegpt.client.MineGPTClient;
import com.xzier9.minegpt.client.agent.Agent;
import net.minecraft.client.Minecraft;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;

/**
 * Vanilla only keeps mining while the mouse is captured by the game window. Let the agent
 * mine while it is running even if the user has switched to another window.
 */
@Mixin(Minecraft.class)
public class MinecraftMixin {
	@ModifyExpressionValue(method = "handleKeybinds",
			at = @At(value = "INVOKE", target = "Lnet/minecraft/client/MouseHandler;isMouseGrabbed()Z"))
	private boolean minegpt$mineWhileUnfocused(boolean grabbed) {
		return grabbed || MineGPTClient.agent().mode() == Agent.Mode.RUNNING;
	}
}
