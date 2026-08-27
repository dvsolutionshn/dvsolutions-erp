import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:onix_mobile/src/widgets/onix_markdown.dart';

void main() {
  testWidgets(
    'renderiza el formato de Onix sin mostrar los marcadores Markdown',
    (tester) async {
      await tester.pumpWidget(
        const MaterialApp(
          home: Scaffold(
            body: OnixMarkdown(
              data: '''### Facturas recientes

- **Factura:** 001-001-01-00000053
- **Total pendiente:** HNL 37,835.00''',
            ),
          ),
        ),
      );

      final renderedText = <String>[
        ...tester
            .widgetList<Text>(find.byType(Text))
            .map(
              (widget) => widget.data ?? widget.textSpan?.toPlainText() ?? '',
            ),
        ...tester
            .widgetList<SelectableText>(find.byType(SelectableText))
            .map(
              (widget) => widget.data ?? widget.textSpan?.toPlainText() ?? '',
            ),
        ...tester
            .widgetList<RichText>(find.byType(RichText))
            .map((widget) => widget.text.toPlainText()),
      ].join(' ');

      expect(renderedText, contains('Facturas recientes'));
      expect(renderedText, contains('001-001-01-00000053'));
      expect(renderedText, contains('HNL 37,835.00'));
      expect(renderedText, isNot(contains('**')));
    },
  );
}
