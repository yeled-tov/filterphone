/// כתובת השרת. אפשר לדרוס בבנייה: --dart-define=FP_API=https://...
const String apiBaseUrl = String.fromEnvironment(
  'FP_API',
  defaultValue: 'https://api.filterphone.com',
);

/// מספר WhatsApp של התמיכה. בבנייה אפשר לדרוס:
/// --dart-define=FP_SUPPORT_WHATSAPP=9725XXXXXXXX
const String supportWhatsAppNumber = String.fromEnvironment(
  'FP_SUPPORT_WHATSAPP',
  defaultValue: '972527186881',
);

const Duration refreshInterval = Duration(seconds: 20);

/// קישור ההצטרפות שמופיע בטקסט השיתוף ("שתפו את FilterPhone"). ניתן לדריסה בבנייה.
const String shareUrl = String.fromEnvironment(
  'FP_SHARE_URL',
  defaultValue: 'https://filterphone.com',
);
