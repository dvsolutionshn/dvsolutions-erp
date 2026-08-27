import 'package:flutter/material.dart';

import 'onix_controller.dart';
import 'screens/chat_screen.dart';
import 'screens/login_screen.dart';
import 'theme.dart';

class OnixMobileApp extends StatefulWidget {
  const OnixMobileApp({super.key});

  @override
  State<OnixMobileApp> createState() => _OnixMobileAppState();
}

class _OnixMobileAppState extends State<OnixMobileApp> {
  late final OnixController _controller;

  @override
  void initState() {
    super.initState();
    _controller = OnixController()..initialize();
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => MaterialApp(
        title: 'Onix',
        debugShowCheckedModeBanner: false,
        theme: buildOnixTheme(),
        home: ListenableBuilder(
          listenable: _controller,
          builder: (context, _) {
            if (_controller.initializing) return const _LaunchScreen();
            if (!_controller.authenticated) return LoginScreen(controller: _controller);
            return ChatScreen(controller: _controller);
          },
        ),
      );
}

class _LaunchScreen extends StatelessWidget {
  const _LaunchScreen();

  @override
  Widget build(BuildContext context) => const Scaffold(
        backgroundColor: onixInk,
        body: Center(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              OnixAvatar(size: 86),
              SizedBox(height: 22),
              Text(
                'ONIX',
                style: TextStyle(
                  color: Colors.white,
                  fontSize: 28,
                  letterSpacing: 8,
                  fontWeight: FontWeight.w700,
                ),
              ),
              SizedBox(height: 24),
              SizedBox(
                width: 22,
                height: 22,
                child: CircularProgressIndicator(color: onixCyan, strokeWidth: 2.5),
              ),
            ],
          ),
        ),
      );
}

class OnixAvatar extends StatelessWidget {
  const OnixAvatar({super.key, this.size = 48});

  final double size;

  @override
  Widget build(BuildContext context) => Container(
        width: size,
        height: size,
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          gradient: const LinearGradient(
            begin: Alignment.topLeft,
            end: Alignment.bottomRight,
            colors: [onixCyan, onixBlue, onixLavender],
          ),
          boxShadow: [
            BoxShadow(color: onixCyan.withValues(alpha: .28), blurRadius: 24, spreadRadius: 2),
          ],
        ),
        child: Icon(Icons.smart_toy_rounded, color: onixInk, size: size * .56),
      );
}

