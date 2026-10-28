package com.filterphone.app

import android.app.Activity
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.ImageDecoder
import android.graphics.Matrix
import android.media.ExifInterface
import android.net.Uri
import android.os.Build
import android.provider.MediaStore
import android.util.Base64
import java.io.ByteArrayOutputStream
import kotlin.math.max
import kotlin.math.min

/**
 * תמונת פרופיל: בוחר התמונות של אנדרואיד (בלי הרשאת גישה לכל הגלריה), ואז ריבוע מוקטן ב-JPEG.
 * הקטנה כבר בטלפון — כדי לשלוח לשרת מעט נתונים (והשרת מקטין שוב, מנקה מטא-דאטה ובודק בסינון).
 */
object AvatarPicker {
    private const val MAX_SIDE = 640          // מה שנשלח לשרת (הוא שומר 256×256)
    private const val DECODE_SIDE = 1600       // פענוח מוקטן — תמונת מצלמה של 50MP לא נטענת במלואה

    /** פותח את בוחר התמונות (אנדרואיד 13+: הבוחר המובנה; לפני — "בחירת קובץ" של תמונות). */
    fun launch(activity: Activity, requestCode: Int): Boolean {
        val intents = buildList {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) add(Intent(MediaStore.ACTION_PICK_IMAGES).setType("image/*"))
            add(Intent(Intent.ACTION_GET_CONTENT).setType("image/*").addCategory(Intent.CATEGORY_OPENABLE))
        }
        for (intent in intents) {
            try {
                @Suppress("DEPRECATION")
                activity.startActivityForResult(intent, requestCode)
                return true
            } catch (_: ActivityNotFoundException) {
                // אין בוחר כזה בטלפון — מנסים את הבא
            }
        }
        return false
    }

    /** התמונה שנבחרה → JPEG ריבועי (base64), מסובב נכון. null — לא תמונה / לא נקראה. */
    fun toJpegBase64(context: Context, uri: Uri): String? {
        val bitmap = decode(context, uri) ?: return null
        val side = min(bitmap.width, bitmap.height)
        val square = Bitmap.createBitmap(bitmap, (bitmap.width - side) / 2, (bitmap.height - side) / 2, side, side)
        val target = min(side, MAX_SIDE)
        val scaled = if (target < side) Bitmap.createScaledBitmap(square, target, target, true) else square
        val out = ByteArrayOutputStream()
        scaled.compress(Bitmap.CompressFormat.JPEG, 90, out)
        return Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP)
    }

    private fun decode(context: Context, uri: Uri): Bitmap? = runCatching {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            // ImageDecoder: גם HEIF, והסיבוב לפי ה-EXIF כבר מוחל
            ImageDecoder.decodeBitmap(ImageDecoder.createSource(context.contentResolver, uri)) { decoder, info, _ ->
                val long = max(info.size.width, info.size.height)
                if (long > DECODE_SIDE) decoder.setTargetSampleSize(long / DECODE_SIDE)
                decoder.allocator = ImageDecoder.ALLOCATOR_SOFTWARE
            }
        } else {
            val resolver = context.contentResolver
            val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            resolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it, null, bounds) }
            val long = max(bounds.outWidth, bounds.outHeight)
            if (long <= 0) return null
            val opts = BitmapFactory.Options().apply { inSampleSize = max(1, long / DECODE_SIDE) }
            val bitmap = resolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it, null, opts) } ?: return null
            val degrees = resolver.openInputStream(uri)?.use {
                when (ExifInterface(it).getAttributeInt(ExifInterface.TAG_ORIENTATION, ExifInterface.ORIENTATION_NORMAL)) {
                    ExifInterface.ORIENTATION_ROTATE_90 -> 90f
                    ExifInterface.ORIENTATION_ROTATE_180 -> 180f
                    ExifInterface.ORIENTATION_ROTATE_270 -> 270f
                    else -> 0f
                }
            } ?: 0f
            if (degrees == 0f) bitmap
            else Bitmap.createBitmap(bitmap, 0, 0, bitmap.width, bitmap.height, Matrix().apply { postRotate(degrees) }, true)
        }
    }.getOrNull()
}
