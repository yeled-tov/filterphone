/// כתובת השרת. אפשר לדרוס בבנייה: --dart-define=FP_API=https://...
const String apiBaseUrl = String.fromEnvironment(
  'FP_API',
  defaultValue: 'https://api.filterphone.com',
);

/// כל כמה זמן לרענן את המצב כשהאפליקציה פתוחה.
const Duration refreshInterval = Duration(seconds: 20);
