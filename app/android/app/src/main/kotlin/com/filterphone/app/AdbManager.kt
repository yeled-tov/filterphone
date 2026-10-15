package com.filterphone.app

import android.content.Context
import android.os.Build
import android.util.Base64
import io.github.muntashirakon.adb.AbsAdbConnectionManager
import java.math.BigInteger
import java.security.KeyFactory
import java.security.KeyPair
import java.security.KeyPairGenerator
import java.security.PrivateKey
import java.security.SecureRandom
import java.security.cert.Certificate
import java.security.cert.CertificateFactory
import java.security.spec.PKCS8EncodedKeySpec
import java.util.Date
import org.bouncycastle.asn1.x500.X500Name
import org.bouncycastle.cert.jcajce.JcaX509CertificateConverter
import org.bouncycastle.cert.jcajce.JcaX509v3CertificateBuilder
import org.bouncycastle.jce.provider.BouncyCastleProvider
import org.bouncycastle.operator.jcajce.JcaContentSignerBuilder

/**
 * מנהל חיבור ה-ADB המקומי של FilterPhone (libadb-android). משמש את [WirelessSetup] כדי להתחבר
 * ל-adbd של הטלפון עצמו דרך "ניפוי אלחוטי" — בלי מחשב ובלי אפליקציה חיצונית.
 *
 * זהות ה-ADB (זוג מפתחות RSA + תעודה חתומה-עצמית) נוצרת פעם אחת ונשמרת מקומית, כך שאחרי אישור
 * ההתאמה הראשונה (pairing) המכשיר "זוכר" את FilterPhone ואפשר להתחבר שוב בלי קוד.
 *
 * אין כאן שום מעקף אבטחה: זה בדיוק מה ש-adb במחשב עושה. מה שאנדרואיד לא מתיר — נכשל ומדווח.
 */
object AdbManager {
    private const val PREFS = "fp_adb"
    private const val K_KEY = "pkcs8"
    private const val K_CERT = "cert"
    private const val DEVICE = "FilterPhone"

    private var manager: Manager? = null

    @Synchronized
    fun get(context: Context): AbsAdbConnectionManager {
        manager?.let { return it }
        return Manager(context.applicationContext).also { manager = it }
    }

    /** סוגרים את החיבור (אחרי שסיימנו להריץ את פקודות ההקמה). */
    @Synchronized
    fun close() {
        runCatching { manager?.close() }
        manager = null
    }

    private class Manager(app: Context) : AbsAdbConnectionManager() {
        private val keyPair: KeyPair = loadOrCreate(app)
        private val cert: Certificate = loadCert(app) ?: selfSigned(app, keyPair)

        init {
            setApi(Build.VERSION.SDK_INT)
        }

        override fun getPrivateKey(): PrivateKey = keyPair.private
        override fun getCertificate(): Certificate = cert
        override fun getDeviceName(): String = DEVICE
    }

    private fun prefs(context: Context) =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    private fun loadOrCreate(context: Context): KeyPair {
        val p = prefs(context)
        val stored = p.getString(K_KEY, null)
        val cert = loadCert(context)
        if (stored != null && cert != null) {
            runCatching {
                val kf = KeyFactory.getInstance("RSA")
                val priv = kf.generatePrivate(PKCS8EncodedKeySpec(Base64.decode(stored, Base64.NO_WRAP)))
                return KeyPair(cert.publicKey, priv)
            }
        }
        val kpg = KeyPairGenerator.getInstance("RSA")
        kpg.initialize(2048, SecureRandom())
        val kp = kpg.generateKeyPair()
        val certificate = selfSigned(context, kp)
        p.edit()
            .putString(K_KEY, Base64.encodeToString(kp.private.encoded, Base64.NO_WRAP))
            .putString(K_CERT, Base64.encodeToString(certificate.encoded, Base64.NO_WRAP))
            .apply()
        return kp
    }

    private fun loadCert(context: Context): Certificate? {
        val raw = prefs(context).getString(K_CERT, null) ?: return null
        return runCatching {
            CertificateFactory.getInstance("X.509")
                .generateCertificate(Base64.decode(raw, Base64.NO_WRAP).inputStream())
        }.getOrNull()
    }

    /** תעודה חתומה-עצמית ל-ADB (כמו ש-adb במחשב יוצר) — BouncyCastle, בלי לרשום ספק גלובלי. */
    private fun selfSigned(context: Context, kp: KeyPair): Certificate {
        val now = Date()
        val expiry = Date(now.time + 10L * 365 * 24 * 3600 * 1000)   // 10 שנים
        val name = X500Name("CN=$DEVICE")
        val serial = BigInteger.valueOf(System.currentTimeMillis())
        val builder = JcaX509v3CertificateBuilder(name, serial, now, expiry, name, kp.public)
        val signer = JcaContentSignerBuilder("SHA256withRSA").build(kp.private)
        val cert = JcaX509CertificateConverter().setProvider(BouncyCastleProvider()).getCertificate(builder.build(signer))
        prefs(context).edit().putString(K_CERT, Base64.encodeToString(cert.encoded, Base64.NO_WRAP)).apply()
        return cert
    }
}
