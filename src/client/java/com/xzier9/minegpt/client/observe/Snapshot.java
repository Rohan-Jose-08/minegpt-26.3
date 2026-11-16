package com.xzier9.minegpt.client.observe;

import com.google.gson.JsonObject;
import com.mojang.blaze3d.platform.NativeImage;
import java.awt.image.BufferedImage;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.util.Base64;
import javax.imageio.ImageIO;

/**
 * Turns a frame of the game into a small picture the brain can show to a model that reads
 * images: shrunk to a given width by averaging pixels, and packed as a JPEG in base64.
 */
public final class Snapshot {
	public static final int DEFAULT_WIDTH = 640;
	public static final int MIN_WIDTH = 160;
	public static final int MAX_WIDTH = 1280;

	private Snapshot() {
	}

	/**
	 * Fills in {@code width}, {@code height}, {@code format} and {@code data} on the reply.
	 *
	 * @param maxWidth the picture is shrunk by a whole factor until it is no wider than this
	 */
	public static void encode(NativeImage frame, int maxWidth, JsonObject reply) throws IOException {
		int width = frame.getWidth();
		int height = frame.getHeight();
		int[] pixels = frame.getPixels();
		int factor = Math.max(1, (int) Math.ceil(width / (double) Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, maxWidth))));
		int outWidth = Math.max(1, width / factor);
		int outHeight = Math.max(1, height / factor);
		BufferedImage image = new BufferedImage(outWidth, outHeight, BufferedImage.TYPE_INT_RGB);
		int[] out = new int[outWidth * outHeight];
		int area = factor * factor;
		for (int y = 0; y < outHeight; y++) {
			for (int x = 0; x < outWidth; x++) {
				int r = 0;
				int g = 0;
				int b = 0;
				for (int dy = 0; dy < factor; dy++) {
					int row = (y * factor + dy) * width + x * factor;
					for (int dx = 0; dx < factor; dx++) {
						int argb = pixels[row + dx];
						r += (argb >> 16) & 0xFF;
						g += (argb >> 8) & 0xFF;
						b += argb & 0xFF;
					}
				}
				out[y * outWidth + x] = ((r / area) << 16) | ((g / area) << 8) | (b / area);
			}
		}
		image.setRGB(0, 0, outWidth, outHeight, out, 0, outWidth);
		ByteArrayOutputStream bytes = new ByteArrayOutputStream(64 * 1024);
		String format = "jpeg";
		if (!ImageIO.write(image, "jpg", bytes)) {
			// No JPEG writer in this Java: PNG is always there.
			bytes.reset();
			format = "png";
			ImageIO.write(image, "png", bytes);
		}
		reply.addProperty("width", outWidth);
		reply.addProperty("height", outHeight);
		reply.addProperty("format", format);
		reply.addProperty("data", Base64.getEncoder().encodeToString(bytes.toByteArray()));
	}
}
