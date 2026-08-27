import 'package:flutter/material.dart';
import 'package:flutter_markdown_plus/flutter_markdown_plus.dart';

import '../theme.dart';

/// Presenta las respuestas de Onix como contenido legible para una pantalla
/// movil: titulos compactos, listas separadas y datos importantes en negrita.
class OnixMarkdown extends StatelessWidget {
  const OnixMarkdown({super.key, required this.data});

  final String data;

  @override
  Widget build(BuildContext context) {
    const body = TextStyle(color: onixInk, height: 1.45, fontSize: 15);

    return MarkdownBody(
      data: data,
      selectable: true,
      styleSheet: MarkdownStyleSheet(
        p: body,
        a: body.copyWith(
          color: const Color(0xFF087E82),
          decoration: TextDecoration.underline,
        ),
        strong: body.copyWith(fontWeight: FontWeight.w800),
        em: body.copyWith(fontStyle: FontStyle.italic),
        h1: body.copyWith(fontSize: 20, fontWeight: FontWeight.w800),
        h2: body.copyWith(fontSize: 18, fontWeight: FontWeight.w800),
        h3: body.copyWith(fontSize: 16, fontWeight: FontWeight.w800),
        listBullet: body.copyWith(
          color: const Color(0xFF087E82),
          fontWeight: FontWeight.w800,
        ),
        blockquote: body.copyWith(color: const Color(0xFF42566A)),
        blockquoteDecoration: const BoxDecoration(
          color: Color(0xFFF1F7F8),
          border: Border(left: BorderSide(color: Color(0xFF12AAA7), width: 3)),
        ),
        code: body.copyWith(
          fontFamily: 'monospace',
          fontSize: 14,
          backgroundColor: const Color(0xFFEAF1F4),
        ),
        blockSpacing: 10,
        listIndent: 22,
        h1Padding: const EdgeInsets.only(bottom: 5),
        h2Padding: const EdgeInsets.only(bottom: 5),
        h3Padding: const EdgeInsets.only(bottom: 4),
      ),
    );
  }
}
