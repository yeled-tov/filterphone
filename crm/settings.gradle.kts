// CRM FilterPhone — אפליקציית המנהל: נפרדת לגמרי מאפליקציית הסינון (app/). Kotlin בלבד, בלי ספריות.
pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "crm-filterphone"
include(":app")
