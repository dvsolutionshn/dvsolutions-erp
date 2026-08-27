import 'package:flutter/material.dart';

const onixInk = Color(0xFF071525);
const onixNavy = Color(0xFF0B2235);
const onixCyan = Color(0xFF2EE6D6);
const onixBlue = Color(0xFF318BFF);
const onixLavender = Color(0xFF9A8CFF);
const onixSurface = Color(0xFFF4F8FB);

ThemeData buildOnixTheme() {
  final scheme = ColorScheme.fromSeed(
    seedColor: onixCyan,
    brightness: Brightness.light,
    primary: const Color(0xFF087E82),
    secondary: onixBlue,
    surface: Colors.white,
  );
  return ThemeData(
    useMaterial3: true,
    colorScheme: scheme,
    scaffoldBackgroundColor: onixSurface,
    fontFamily: 'Roboto',
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: Colors.white,
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(18),
        borderSide: const BorderSide(color: Color(0xFFD5E1E8)),
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(18),
        borderSide: const BorderSide(color: Color(0xFFD5E1E8)),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(18),
        borderSide: const BorderSide(color: Color(0xFF12AAA7), width: 1.5),
      ),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        backgroundColor: const Color(0xFF087E82),
        foregroundColor: Colors.white,
        minimumSize: const Size.fromHeight(54),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(18)),
      ),
    ),
  );
}
