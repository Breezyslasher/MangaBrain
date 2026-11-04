# Moshi reflects over Kotlin data classes, so their members must survive.
-keep class com.mangabrain.app.data.api.** { *; }
-keepclassmembers class kotlin.Metadata { *; }
-dontwarn okhttp3.**
-dontwarn retrofit2.**
