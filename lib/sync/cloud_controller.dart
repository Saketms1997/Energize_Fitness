import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;

import '../core/providers.dart';
import '../repositories/settings_store.dart';
import '../services/session_controller.dart';
import 'cloud_auth.dart';
import 'remote_gateway.dart';
import 'sync_service.dart';

final httpClientProvider = Provider<http.Client>((ref) {
  final c = http.Client();
  ref.onDispose(c.close);
  return c;
});

final secretStoreProvider = Provider<SecretStore>((_) => SecureSecretStore());

final settingsStoreProvider = Provider<SettingsStore>(
  (ref) => SettingsStore(ref.watch(databaseProvider), ref.watch(clockProvider)),
);

class CloudState {
  const CloudState({
    this.projectUrl,
    this.anonKey,
    this.email,
    this.signedIn = false,
    this.busy = false,
    this.message,
    this.lastSync,
    this.pending = 0,
    this.whatsappAuto = false,
  });

  final String? projectUrl;
  final String? anonKey;
  final String? email;
  final bool signedIn;
  final bool busy;
  final String? message;
  final DateTime? lastSync;
  final int pending;
  final bool whatsappAuto;

  bool get configured => projectUrl != null && anonKey != null;

  CloudState copyWith({
    String? projectUrl,
    String? anonKey,
    String? email,
    bool? signedIn,
    bool? busy,
    String? message,
    bool clearMessage = false,
    DateTime? lastSync,
    int? pending,
    bool? whatsappAuto,
  }) => CloudState(
    projectUrl: projectUrl ?? this.projectUrl,
    anonKey: anonKey ?? this.anonKey,
    email: email ?? this.email,
    signedIn: signedIn ?? this.signedIn,
    busy: busy ?? this.busy,
    message: clearMessage ? null : (message ?? this.message),
    lastSync: lastSync ?? this.lastSync,
    pending: pending ?? this.pending,
    whatsappAuto: whatsappAuto ?? this.whatsappAuto,
  );
}

/// Owns cloud connection state and the background sync timer.
class CloudController extends Notifier<CloudState> {
  static const syncInterval = Duration(minutes: 5);
  Timer? _timer;

  @override
  CloudState build() {
    ref.onDispose(() => _timer?.cancel());
    // Re-read whenever the signed-in owner changes.
    ref.listen(sessionProvider, (_, next) {
      if (next == null) {
        _timer?.cancel();
        state = const CloudState();
      } else {
        Future.microtask(refresh);
      }
    });
    return const CloudState();
  }

  String? get _gymId => ref.read(sessionProvider)?.gymId;
  SettingsStore get _settings => ref.read(settingsStoreProvider);

  CloudAuth? _auth(CloudState s) => s.configured
      ? CloudAuth(
          client: ref.read(httpClientProvider),
          projectUrl: s.projectUrl!,
          anonKey: s.anonKey!,
          store: ref.read(secretStoreProvider),
          clock: ref.read(clockProvider),
        )
      : null;

  /// Access token for the edge-function sender and the sync gateway.
  Future<String?> accessToken() async => await _auth(state)?.accessToken();

  SyncService? _syncService() {
    final s = state;
    final auth = _auth(s);
    if (auth == null) return null;
    return SyncService(
      ref.read(databaseProvider),
      ref.read(clockProvider),
      SupabaseGateway(
        client: ref.read(httpClientProvider),
        projectUrl: s.projectUrl!,
        anonKey: s.anonKey!,
        accessToken: auth.accessToken,
      ),
    );
  }

  Future<void> refresh() async {
    final gym = _gymId;
    if (gym == null) return;
    final url = await _settings.get(gym, SettingsStore.cloudUrl);
    final key = await _settings.get(gym, SettingsStore.cloudAnonKey);
    final auto = await _settings.get(gym, SettingsStore.autoWhatsapp) == 'on';
    var s = CloudState(projectUrl: url, anonKey: key, whatsappAuto: auto);
    final auth = _auth(s);
    if (auth != null) {
      s = s.copyWith(email: await auth.email, signedIn: await auth.isSignedIn);
    }
    state = s;
    if (s.signedIn) {
      final svc = _syncService()!;
      state = state.copyWith(
        pending: await svc.pendingCount(gym),
        lastSync: await svc.lastSuccess(gym),
      );
      _startTimer();
    } else {
      _timer?.cancel();
    }
  }

  void _startTimer() {
    _timer?.cancel();
    _timer = Timer.periodic(syncInterval, (_) {
      if (!state.busy && state.signedIn) syncNow(quiet: true);
    });
  }

  Future<void> setWhatsappAuto(bool on) async {
    final gym = _gymId;
    if (gym == null) return;
    await _settings.set(gym, SettingsStore.autoWhatsapp, on ? 'on' : 'off');
    state = state.copyWith(whatsappAuto: on);
  }

  /// Saves the project, signs in (or creates the account) and runs a sync.
  Future<void> connect({
    required String projectUrl,
    required String anonKey,
    required String email,
    required String password,
    bool createAccount = false,
  }) async {
    final gym = _gymId;
    if (gym == null) return;
    final url = projectUrl.trim().replaceAll(RegExp(r'/+$'), '');
    final uri = Uri.tryParse(url);
    if (uri == null || uri.scheme != 'https' || uri.host.isEmpty) {
      state = state.copyWith(
        message: 'The project URL must start with https://',
      );
      return;
    }
    if (anonKey.trim().isEmpty || email.trim().isEmpty || password.isEmpty) {
      state = state.copyWith(message: 'Fill in every field.');
      return;
    }
    state = state.copyWith(busy: true, clearMessage: true);
    try {
      await _settings.set(gym, SettingsStore.cloudUrl, url);
      await _settings.set(gym, SettingsStore.cloudAnonKey, anonKey.trim());
      state = CloudState(
        projectUrl: url,
        anonKey: anonKey.trim(),
        busy: true,
        whatsappAuto: state.whatsappAuto,
      );
      final auth = _auth(state)!;
      if (createAccount) {
        await auth.signUp(email, password);
      } else {
        await auth.signIn(email, password);
      }
      state = state.copyWith(signedIn: true, email: email.trim());
      _startTimer();
      await _runSync(quiet: false);
    } on CloudAuthException catch (e) {
      state = state.copyWith(busy: false, message: e.message);
    } finally {
      state = state.copyWith(busy: false);
    }
  }

  Future<SyncReport?> syncNow({bool quiet = false}) async {
    if (state.busy) return null;
    return _runSync(quiet: quiet);
  }

  Future<SyncReport?> _runSync({required bool quiet}) async {
    final gym = _gymId;
    final svc = _syncService();
    if (gym == null || svc == null || !state.signedIn) return null;
    state = state.copyWith(busy: true, clearMessage: true);
    final report = await svc.sync(gym);
    state = state.copyWith(
      busy: false,
      pending: await svc.pendingCount(gym),
      lastSync: await svc.lastSuccess(gym),
      message: report.ok
          ? (quiet
                ? null
                : 'Synced: sent ${report.pushed}, received ${report.pulled}'
                      '${report.conflicts == 0 ? '' : ', ${report.conflicts} conflict(s) resolved by the server version'}'
                      '${report.applyFailures == 0 ? '' : ', ${report.applyFailures} record(s) could not be stored (see audit log)'}.')
          : 'Sync problem: ${report.error}',
    );
    return report;
  }

  /// Signs out of the cloud. Local data stays; sync stops.
  Future<void> disconnect() async {
    _timer?.cancel();
    await _auth(state)?.signOut();
    state = CloudState(
      projectUrl: state.projectUrl,
      anonKey: state.anonKey,
      message: 'Disconnected. Your data stays on this device.',
    );
  }
}

final cloudControllerProvider = NotifierProvider<CloudController, CloudState>(
  CloudController.new,
);
