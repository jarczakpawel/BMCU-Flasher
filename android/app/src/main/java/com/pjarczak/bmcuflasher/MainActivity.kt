package com.pjarczak.bmcuflasher

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbManager
import android.os.Build
import android.os.Bundle
import android.text.method.LinkMovementMethod
import android.text.util.Linkify
import android.view.View
import android.view.WindowManager
import android.widget.AdapterView
import android.widget.ArrayAdapter
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import com.hoho.android.usbserial.driver.UsbSerialPort
import com.hoho.android.usbserial.driver.UsbSerialProber
import com.pjarczak.bmcuflasher.databinding.ActivityMainBinding
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import kotlin.concurrent.thread

class MainActivity : AppCompatActivity() {
  private val REQUEST_CODE_PICK_FILE = 1001
  private var selectedFirmwareFile: File? = null
  
  private lateinit var b: ActivityMainBinding
  private lateinit var usb: UsbManager
  private lateinit var i18n: I18n

  private val ACTION_USB_PERMISSION = "com.pjarczak.bmcuflasher.USB_PERMISSION"
  private val lock = Object()
  private var permDevId: Int = -1
  private var permGranted: Boolean? = null

  private data class DevItem(val dev: UsbDevice, val label: String)
  private data class AdapterPreset(val label: String, val vid: Int, val pid: Int, val baud: Int, val fastBaud: Int, val noFast: Boolean)

  private val devs = ArrayList<DevItem>()
  private var flashing = false

  private val LOG_MAX_CHARS = 200_000
  private val LOG_TRIM_CHARS = 60_000

  private val APP_URL = "https://github.com/jarczakpawel/BMCU-Flasher"
  private val FW_URL = "https://github.com/jarczakpawel/BMCU-C-PJARCZAK"

  private val modeIds = listOf("usb", "ttl")
  private val forceIds = listOf(FirmwareSelector.FORCE_STD, FirmwareSelector.FORCE_SOFT, FirmwareSelector.FORCE_HF)
  private val ttlAdapters = listOf(
    AdapterPreset("CH340/CH341 (WCH)", 0x1A86, 0x7523, 115200, 1_000_000, false),
    AdapterPreset("CP2102/CP210x (Silabs)", 0x10C4, 0xEA60, 115200, 921_600, false),
    AdapterPreset("FT232R (FTDI)", 0x0403, 0x6001, 115200, 1_000_000, false),
    AdapterPreset("PL2303 (Prolific)", 0x067B, 0x2303, 115200, 460_800, false),
    AdapterPreset("CH9102 (WCH)", 0x1A86, 0x55D4, 115200, 1_000_000, false),
    AdapterPreset("CH343 (WCH)", 0x1A86, 0x55D3, 115200, 1_000_000, false),
    AdapterPreset("ALL", 0, 0, 115200, 1_000_000, false)
  )

  private val rx = object : BroadcastReceiver() {
    override fun onReceive(ctx: Context, intent: Intent) {
      when (intent.action) {
        ACTION_USB_PERMISSION -> {
          val d = if (Build.VERSION.SDK_INT >= 33) {
            intent.getParcelableExtra(UsbManager.EXTRA_DEVICE, UsbDevice::class.java)
          } else {
            @Suppress("DEPRECATION")
            intent.getParcelableExtra(UsbManager.EXTRA_DEVICE)
          }
          val ok = intent.getBooleanExtra(UsbManager.EXTRA_PERMISSION_GRANTED, false)
          if (d != null) {
            synchronized(lock) {
              permDevId = d.deviceId
              permGranted = ok
              lock.notifyAll()
            }
          }
        }
        UsbManager.ACTION_USB_DEVICE_DETACHED,
        UsbManager.ACTION_USB_DEVICE_ATTACHED -> refreshDevices()
      }
    }
  }

  override fun onCreate(savedInstanceState: Bundle?) {
    super.onCreate(savedInstanceState)
    
    b.btnSelectFile.setOnClickListener { selectLocalFirmware() }
    
    b = ActivityMainBinding.inflate(layoutInflater)
    setContentView(b.root)

    usb = getSystemService(Context.USB_SERVICE) as UsbManager
    i18n = I18n(this)

    applyTexts()
    setupSelectors()

    b.txtLinks.text = "App: $APP_URL\nFirmware: $FW_URL"
    Linkify.addLinks(b.txtLinks, Linkify.WEB_URLS)
    b.txtLinks.movementMethod = LinkMovementMethod.getInstance()

    b.txtLog.setText("", android.widget.TextView.BufferType.EDITABLE)

    b.btnRefresh.setOnClickListener { refreshDevices() }
    b.btnFlash.setOnClickListener { startFlash() }

    val f = IntentFilter().apply {
      addAction(ACTION_USB_PERMISSION)
      addAction(UsbManager.ACTION_USB_DEVICE_ATTACHED)
      addAction(UsbManager.ACTION_USB_DEVICE_DETACHED)
    }
    if (Build.VERSION.SDK_INT >= 33) registerReceiver(rx, f, Context.RECEIVER_NOT_EXPORTED) else registerReceiver(rx, f)

    applyModeUi()
    refreshDevices()
  }

  override fun onDestroy() {
    try { unregisterReceiver(rx) } catch (_: Throwable) {}
    super.onDestroy()
  }

  private fun applyTexts() {
    b.txtTitle.text = i18n.t("android_title")
    b.txtDeviceLabel.text = i18n.t("android_device_label")
    b.txtWarnTitle.text = i18n.t("warn_title")
    b.txtWarnText.text = i18n.t("warn_text")
    b.txtModeLabel.text = i18n.t("mode_label")
    b.txtAdapterLabel.text = i18n.t("adapter_label")
    b.btnRefresh.text = i18n.t("android_refresh")
    b.btnFlash.text = i18n.t("android_flash")
    b.txtOnlineTitle.text = i18n.t("online_title")
    b.txtForceLabel.text = i18n.t("online_force_label")
    b.txtSlotLabel.text = i18n.t("online_slot_label")
    b.txtRetractLabel.text = i18n.t("online_retract_label")
    b.chkAutoload.text = i18n.t("online_autoload")
    b.chkRgb.text = i18n.t("online_rgb")
    b.chkNoFast.text = i18n.t("no_fast")
    b.txtLogTitle.text = i18n.t("android_log_title")
    b.txtTtlHint.text = i18n.t("ttl_hint_boot_reset")
    updateDeviceHint()
    updateForceDesc()
  }

  private fun setupSelectors() {
    b.spnMode.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, listOf(i18n.t("mode_usb"), i18n.t("mode_ttl")))
    b.spnMode.setSelection(0)
    b.spnMode.onItemSelected {
      applyModeUi()
      refreshDevices()
    }

    b.spnAdapter.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, ttlAdapters.map { it.label })
    b.spnAdapter.setSelection(0)
    b.spnAdapter.onItemSelected {
      applyAdapterPreset()
      if (currentModeId() == "ttl") refreshDevices()
    }

    b.spnForce.adapter = ArrayAdapter(
      this,
      android.R.layout.simple_spinner_dropdown_item,
      listOf(i18n.t("online_force_std"), i18n.t("online_force_soft"), i18n.t("online_force_hf"))
    )
    b.spnForce.setSelection(0)
    b.spnForce.onItemSelected { updateForceDesc() }

    b.spnSlot.adapter = ArrayAdapter(
      this,
      android.R.layout.simple_spinner_dropdown_item,
      listOf(FirmwareSelector.SLOT_SOLO, FirmwareSelector.SLOT_A, FirmwareSelector.SLOT_B, FirmwareSelector.SLOT_C, FirmwareSelector.SLOT_D)
    )
    b.spnSlot.setSelection(0)
    updateRetracts()
    b.spnSlot.onItemSelected { updateRetracts() }
  }

  private fun currentModeId(): String {
    val idx = b.spnMode.selectedItemPosition
    return if (idx in modeIds.indices) modeIds[idx] else "usb"
  }

  private fun currentForceId(): String {
    val idx = b.spnForce.selectedItemPosition
    return if (idx in forceIds.indices) forceIds[idx] else FirmwareSelector.FORCE_STD
  }

  private fun selectedAdapter(): AdapterPreset {
    val idx = b.spnAdapter.selectedItemPosition
    return if (idx in ttlAdapters.indices) ttlAdapters[idx] else ttlAdapters[0]
  }

  private fun applyAdapterPreset() {
    val preset = selectedAdapter()
    b.chkNoFast.isChecked = preset.noFast
  }

  private fun updateForceDesc() {
    val desc = when (currentForceId()) {
      FirmwareSelector.FORCE_SOFT -> i18n.t("online_force_soft_desc")
      FirmwareSelector.FORCE_HF -> i18n.t("online_force_hf_desc")
      else -> i18n.t("online_force_std_desc")
    }
    b.txtForceDesc.text = desc
  }

  private fun updateRetracts() {
    val slot = b.spnSlot.selectedItem as String
    val vals = FirmwareSelector.retractValuesForSlot(slot)
    b.spnRetract.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, vals)
    b.spnRetract.setSelection(0)
  }

  private fun applyModeUi() {
    val ttl = currentModeId() == "ttl"
    b.rowAdapter.visibility = if (ttl) View.VISIBLE else View.GONE
    if (!ttl) showTtlHint(false)
    updateDeviceHint()
  }

  private fun updateDeviceHint() {
    b.txtDeviceHint.text = if (currentModeId() == "ttl") i18n.t("android_device_hint_ttl") else i18n.t("android_device_hint_usb")
  }

  private fun refreshDevices() {
    devs.clear()
    val prober = UsbSerialProber.getDefaultProber()
    val mode = currentModeId()

    for (d in usb.deviceList.values) {
      if (mode == "usb") {
        if (d.vendorId == 0x1A86 && d.productId == 0x7523) {
          devs.add(DevItem(d, "CH340 ${d.deviceName}"))
        }
      } else {
        if (prober.probeDevice(d) == null) continue
        val preset = selectedAdapter()
        if (preset.vid != 0 || preset.pid != 0) {
          if (d.vendorId != preset.vid || d.productId != preset.pid) continue
        }
        val name = d.productName?.takeIf { it.isNotBlank() } ?: d.deviceName
        val label = "$name (vid=0x%04X pid=0x%04X)".format(d.vendorId, d.productId)
        devs.add(DevItem(d, label))
      }
    }

    val labels = if (devs.isEmpty()) listOf("-") else devs.map { it.label }
    b.spnDevice.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, labels)
    if (devs.isNotEmpty()) b.spnDevice.setSelection(0)
  }

  private fun log(level: String, msg: String) {
    val ts = SimpleDateFormat("HH:mm:ss", Locale.US).format(Date())
    runOnUiThread {
      val e = b.txtLog.editableText
      e.append(ts)
      e.append('\t')
      e.append(level)
      e.append('\t')
      e.append(msg)
      e.append('\n')
      if (e.length > LOG_MAX_CHARS) {
        val del = LOG_TRIM_CHARS.coerceAtMost(e.length)
        e.delete(0, del)
      }

      if (currentModeId() == "ttl") {
        if (level == "ACTION" && msg.contains("enter bootloader now")) showTtlHint(true)
        if (msg.contains("bootloader detected")) showTtlHint(false)
      }
    }
  }

  private fun showTtlHint(show: Boolean) {
    b.txtTtlHint.visibility = if (show && currentModeId() == "ttl") View.VISIBLE else View.GONE
  }

  private fun prog(pct: Int) {
    runOnUiThread { b.pbar.progress = pct.coerceIn(0, 100) }
  }

  private fun pickDevice(): UsbDevice? {
    if (devs.isEmpty()) return null
    val idx = b.spnDevice.selectedItemPosition
    if (idx !in devs.indices) return devs[0].dev
    return devs[idx].dev
  }

  private fun ensurePermission(dev: UsbDevice, timeoutMs: Long = 15000): Boolean {
    if (usb.hasPermission(dev)) return true

    val flags = if (Build.VERSION.SDK_INT >= 31) {
      PendingIntent.FLAG_MUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
    } else {
      PendingIntent.FLAG_UPDATE_CURRENT
    }
    val pi = PendingIntent.getBroadcast(this, 0, Intent(ACTION_USB_PERMISSION).setPackage(packageName), flags)

    synchronized(lock) {
      permDevId = dev.deviceId
      permGranted = null
      usb.requestPermission(dev, pi)

      val end = System.currentTimeMillis() + timeoutMs
      while (permGranted == null && System.currentTimeMillis() < end) {
        try { lock.wait(200) } catch (_: Throwable) {}
      }
      val ok = permGranted == true && permDevId == dev.deviceId
      permGranted = null
      return ok
    }
  }

  private fun confirmSafety(onOk: () -> Unit) {
    AlertDialog.Builder(this)
      .setTitle(i18n.t("warn_title"))
      .setMessage(i18n.t("warn_text"))
      .setPositiveButton(i18n.t("ok")) { _d, _w -> onOk() }
      .setNegativeButton(i18n.t("cancel")) { _d, _w -> }
      .show()
  }

  private fun startFlash() {
    if (flashing) {
      Toast.makeText(this, i18n.t("android_busy"), Toast.LENGTH_SHORT).show()
      return
    }

    val dev = pickDevice()
    if (dev == null) {
      val msg = if (currentModeId() == "ttl") i18n.t("android_no_device_ttl") else i18n.t("android_no_device_usb")
      Toast.makeText(this, msg, Toast.LENGTH_LONG).show()
      return
    }

    confirmSafety { startFlashImpl(dev) }
  }

  private fun startFlashImpl(dev: UsbDevice) {
    if (flashing) return

    val mode = currentModeId()
    val force = currentForceId()
    val slot = b.spnSlot.selectedItem as String
    val retract = b.spnRetract.selectedItem as String
    val autoload = b.chkAutoload.isChecked
    val rgb = b.chkRgb.isChecked
    val preset = if (mode == "ttl") selectedAdapter() else ttlAdapters[0]
    val noFast = b.chkNoFast.isChecked || preset.noFast

    flashing = true
    window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
    b.btnFlash.isEnabled = false
    b.pbar.progress = 0
    showTtlHint(false)

    thread(start = true, isDaemon = true) {
      var conn: android.hardware.usb.UsbDeviceConnection? = null
      var port: UsbSerialPort? = null

      try {
        if (!ensurePermission(dev)) throw RuntimeException(i18n.t("android_perm_denied"))

        val sel = FirmwareSelector.build(force, slot, retract, autoload, rgb)
        log("INFO", "online: selected ${sel.relPath}")
        prog(0)

        val fwCache = File(cacheDir, "firmwares")
        val (fwFile, ver) = RemoteFirmware.download(sel.relPath, fwCache, ::log) { p -> prog(p) }
        log("INFO", "online: using $ver (${fwFile.name})")
        prog(0)

        val prober = UsbSerialProber.getDefaultProber()
        val driver = prober.probeDevice(dev) ?: throw RuntimeException(i18n.t("android_probe_fail"))
        conn = usb.openDevice(dev) ?: throw RuntimeException(i18n.t("android_open_fail"))
        port = driver.ports[0]

        port!!.open(conn)
        port!!.setParameters(preset.baud, 8, UsbSerialPort.STOPBITS_1, UsbSerialPort.PARITY_NONE)
        if (mode == "usb") {
          try { port!!.setDTR(true); port!!.setRTS(true) } catch (_: Throwable) {}
        }

        val fwBytes = fwFile.readBytes()

        log("INFO", "open port=${dev.deviceName} mode=$mode")
        if (mode == "usb") {
          BmcuFlasher.flashUsb(
            port = port!!,
            firmware = fwBytes,
            log = ::log,
            progress = ::prog,
            baud = preset.baud,
            fastBaud = preset.fastBaud,
            noFast = noFast,
            verify = true
          )
        } else {
          BmcuFlasher.flashTtl(
            port = port!!,
            firmware = fwBytes,
            log = ::log,
            progress = ::prog,
            baud = preset.baud,
            fastBaud = preset.fastBaud,
            noFast = noFast,
            verify = true
          )
        }

        try {
          if (fwFile.isFile) {
            fwFile.delete()
            log("INFO", "online: cache removed (${fwFile.name})")
          }
        } catch (_: Throwable) {}

        runOnUiThread { Toast.makeText(this, i18n.t("android_done"), Toast.LENGTH_SHORT).show() }
      } catch (e: Throwable) {
        log("ERROR", e.message ?: e.toString())
        runOnUiThread { Toast.makeText(this, e.message ?: i18n.t("err_generic"), Toast.LENGTH_LONG).show() }
      } finally {
        if (mode == "usb") {
          try { port?.setDTR(true); port?.setRTS(true) } catch (_: Throwable) {}
        }
        try { port?.close() } catch (_: Throwable) {}
        try { conn?.close() } catch (_: Throwable) {}

        runOnUiThread {
          b.btnFlash.isEnabled = true
          showTtlHint(false)
          window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        }
        flashing = false
      }
    }
  }

  private fun android.widget.Spinner.onItemSelected(fn: () -> Unit) {
    onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
      override fun onItemSelected(parent: AdapterView<*>, view: android.view.View?, position: Int, id: Long) {
        fn()
      }

      override fun onNothingSelected(parent: AdapterView<*>) {}
    }
  }

    private fun selectLocalFirmware() {
    // 请求存储权限
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
      if (!android.os.Environment.isExternalStorageManager()) {
        val intent = Intent(android.provider.Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION)
        intent.data = android.net.Uri.parse("package:$packageName")
        startActivity(intent)
        Toast.makeText(this, "Please grant file access permission", Toast.LENGTH_LONG).show()
        return
      }
    } else {
      if (checkSelfPermission(android.Manifest.permission.READ_EXTERNAL_STORAGE) != android.content.pm.PackageManager.PERMISSION_GRANTED) {
        requestPermissions(arrayOf(android.Manifest.permission.READ_EXTERNAL_STORAGE), 1000)
        return
      }
    }
  
    // 打开文件选择器
    val intent = Intent(Intent.ACTION_GET_CONTENT)
    intent.type = "application/octet-stream"
    intent.addCategory(Intent.CATEGORY_OPENABLE)
    startActivityForResult(Intent.createChooser(intent, "Select firmware file"), REQUEST_CODE_PICK_FILE)
  }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
  super.onActivityResult(requestCode, resultCode, data)
  if (requestCode == REQUEST_CODE_PICK_FILE && resultCode == RESULT_OK && data != null) {
    val uri = data.data ?: return
    try {
      // 复制文件到应用缓存目录（避免Content URI权限问题）
      val inputStream = contentResolver.openInputStream(uri) ?: throw RuntimeException("Failed to open file")
      val fileName = uri.lastPathSegment ?: "firmware.bin"
      val tempFile = File(cacheDir, fileName)
      
      inputStream.use { input ->
        tempFile.outputStream().use { output ->
          input.copyTo(output)
        }
      }
      
      // 验证文件是否为bin格式
      if (!fileName.endsWith(".bin", ignoreCase = true)) {
        throw RuntimeException("Please select a .bin file")
      }
      
      selectedFirmwareFile = tempFile
      b.txtSelectedFile.text = fileName
      log("INFO", "local: selected $fileName (${tempFile.length()} bytes)")
      
    } catch (e: Exception) {
      Toast.makeText(this, "Failed to select file: ${e.message}", Toast.LENGTH_LONG).show()
      log("ERROR", "local: ${e.message}")
      selectedFirmwareFile = null
      b.txtSelectedFile.text = "No file selected"
    }
  }
}
}
