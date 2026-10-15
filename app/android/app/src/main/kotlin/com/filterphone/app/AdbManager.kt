package com.filterphone.app

import android.content.Context
import android.os.Build
import android.util.Base64
import io.github.muntashirakon.adb.AbsAdbConnectionManager
import java.security.KeyFactory
import java.security.KeyPair
import java.security.KeyPairGenerator
import java.security.PrivateKey
import java.security.SecureRandom
import java.security.cert.Certificate
import java.security.cert.CertificateFactory
import java.security.spec.PKCS8EncodedKeySpec
import java.util.Date
import java.util.Random
import sun.security.x509.AlgorithmId
import sun.security.x509.CertificateAlgorithmId
import sun.security.x509.CertificateExtensions
import sun.security.x509.CertificateSerialNumber
import sun.security.x509.CertificateValidity
import sun.security.x509.CertificateVersion
import sun.security.x509.CertificateX509Key
import sun.security.x509.KeyIdentifier
import sun.security.x509.PrivateKeyUsageExtension
import sun.security.x509.SubjectKeyIdentifierExtension
import sun.security.x509.X500Name
import sun.security.x509.X509CertImpl
import sun.security.x509.X509CertInfo

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

    private class Manager(private val app: Context) : AbsAdbConnectionManager() {
        private val keyPair: KeyPair = loadOrCreate(app)
        private val cert: Certificate

        init {
            setApi(Build.VERSION.SDK_INT)
            cert = loadCert(app) ?: selfSigned(app, keyPair)
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
        if (stored != null) {
            runCatching {
                val kf = KeyFactory.getInstance("RSA")
                val priv = kf.generatePrivate(PKCS8EncodedKeySpec(Base64.decode(stored, Base64.NO_WRAP)))
                // המפתח הציבורי נגזר מהתעודה
                val cert = loadCert(context)
                if (cert != null) return KeyPair(cert.publicKey, priv)
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

    /** תעודה חתומה-עצמית ל-ADB (כמו ש-adb במחשב יוצר). sun.security.x509 מגיע מ-sun-security-android. */
    private fun selfSigned(context: Context, kp: KeyPair): Certificate {
        val algorithm = "SHA512withRSA"
        val now = Date()
        val expiry = Date(now.time + 10L * 365 * 24 * 3600 * 1000)   // 10 שנים
        val name = X500Name("CN=$DEVICE")
        val extensions = CertificateExtensions()
        extensions.set(
            SubjectKeyIdentifierExtension.NAME,
            SubjectKeyIdentifierExtension(KeyIdentifier(kp.public).identifier),
        )
        extensions.set(PrivateKeyUsageExtension.NAME, PrivateKeyUsageExtension(now, expiry))
        val info = X509CertInfo()
        info.set(X509CertInfo.VERSION, CertificateVersion(CertificateVersion.V3))
        info.set(X509CertInfo.SERIAL_NUMBER, CertificateSerialNumber(Random().nextInt() and 0x7fffffff))
        info.set(X509CertInfo.ALGORITHM_ID, CertificateAlgorithmId(AlgorithmId.get(algorithm)))
        info.set(X509CertInfo.SUBJECT, name)
        info.set(X509CertInfo.KEY, CertificateX509Key(kp.public))
        info.set(X509CertInfo.VALIDITY, CertificateValidity(now, expiry))
        info.set(X509CertInfo.ISSUER, name)
        info.set(X509CertInfo.EXTENSIONS, extensions)
        val certImpl = X509CertImpl(info)
        certImpl.sign(kp.private, algorithm)
        // נשמר כדי שהמפתח הציבורי יתאים בפעם הבאה
        prefs(context).edit().putString(K_CERT, Base64.encodeToString(certImpl.encoded, Base64.NO_WRAP)).apply()
        return certImpl
    }
}
