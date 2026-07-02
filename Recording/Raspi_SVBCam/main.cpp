
#include <QCheckBox>
#include <QApplication>
#include <QWidget>
#include <QLabel>
#include <QPushButton>
#include <QComboBox>
#include <QSpinBox>
#include <QLineEdit>
#include <QTextEdit>
#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QGridLayout>
#include <QFileDialog>
#include <QTimer>
#include <QImage>
#include <QPixmap>
#include <QMessageBox>
#include <QJsonDocument>
#include <QJsonObject>
#include <QFile>
#include <QDateTime>
#include <QRadioButton>
#include <QButtonGroup>
#include <QGroupBox>
#include <QDir>

#include <opencv2/opencv.hpp>
#include <filesystem>
#include <chrono>
#include <vector>

#include "SVBCameraSDK.h"

namespace fs = std::filesystem;

class FlyStressCapture : public QWidget
{
    Q_OBJECT

public:
    FlyStressCapture(QWidget *parent = nullptr) : QWidget(parent)
    {
        setWindowTitle("FlyStress SVB PRO");
        resize(1250, 850);

        preview = new QLabel("Live Preview");
        preview->setAlignment(Qt::AlignCenter);
        preview->setMinimumSize(900, 520);
        preview->setStyleSheet("background-color: black; color: white; font-size: 18px;");

        cameraStatus = new QLabel("Camera: Not connected");

        experimentName = new QLineEdit;
        experimentName->setPlaceholderText("Example: FlyStress_Day7_Control");

        notes = new QTextEdit;
        notes->setPlaceholderText("Experiment notes...");
        notes->setMaximumHeight(70);

        outputDir = new QLineEdit("/home/admin/Desktop/VideosA");
        browseButton = new QPushButton("Browse");

        resolutionBox = new QComboBox;
        resolutionBox->addItem("1920 x 1080", QSize(1920, 1080));
        resolutionBox->addItem("1280 x 720", QSize(1280, 720));
        resolutionBox->addItem("800 x 600", QSize(800, 600));
        resolutionBox->addItem("640 x 480", QSize(640, 480));

        fpsBox = new QSpinBox;
        fpsBox->setRange(1, 60);
        fpsBox->setValue(10);

        timedRadio = new QRadioButton("Timed");
        continuousRadio = new QRadioButton("Continuous");
        timedRadio->setChecked(true);

        modeGroup = new QButtonGroup(this);
        modeGroup->addButton(timedRadio);
        modeGroup->addButton(continuousRadio);

        hoursBox = new QSpinBox;
        minutesBox = new QSpinBox;
        secondsBox = new QSpinBox;

        hoursBox->setRange(0, 999);
        minutesBox->setRange(0, 59);
        secondsBox->setRange(0, 59);

        hoursBox->setValue(0);
        minutesBox->setValue(10);
        secondsBox->setValue(0);

        exposureBox = new QSpinBox;
        exposureBox->setRange(8, 20000000);
        exposureBox->setValue(1000);
        exposureBox->setSuffix(" us");

        gainBox = new QSpinBox;
        gainBox->setRange(0, 450);
        gainBox->setValue(0);

        savedSettingsBox = new QComboBox;
        settingNameEntry = new QLineEdit;
        settingNameEntry->setPlaceholderText("Preset name");

        loadSettingsButton = new QPushButton("Load");
        saveSettingsButton = new QPushButton("Save Settings");

        screenshotFormatBox = new QComboBox;
        screenshotFormatBox->addItem("PNG");
        screenshotFormatBox->addItem("TIFF");

        videoFormatBox = new QComboBox;
        videoFormatBox->addItem("AVI MJPEG");
        videoFormatBox->addItem("MP4");

        screenshotButton = new QPushButton("Screenshot");
        recordButton = new QPushButton("Start Recording");
        stopButton = new QPushButton("Stop Recording");
        stopButton->setEnabled(false);

        statusLabel = new QLabel("Status: Ready");

        advancedBox = new QGroupBox("Advanced Camera Settings");
        advancedBox->setCheckable(true);
        advancedBox->setChecked(false);

        gammaBox = new QSpinBox;
        gammaBox->setRange(0, 100);
        gammaBox->setValue(100);

        wbRBox = new QSpinBox;
        wbGBox = new QSpinBox;
        wbBBox = new QSpinBox;

        wbRBox->setRange(0, 511);
        wbGBox->setRange(0, 511);
        wbBBox->setRange(0, 511);

        wbRBox->setValue(128);
        wbGBox->setValue(128);
        wbBBox->setValue(128);

        droppedFramesLabel = new QLabel("Dropped frames: 0");
	autoStretchBox = new QCheckBox("Auto Stretch Preview");
	autoStretchBox->setChecked(true);
	

        auto experimentLayout = new QGridLayout;
        experimentLayout->addWidget(new QLabel("Experiment Name"), 0, 0);
        experimentLayout->addWidget(experimentName, 0, 1);
        experimentLayout->addWidget(new QLabel("Notes"), 1, 0);
        experimentLayout->addWidget(notes, 1, 1);
        experimentLayout->addWidget(new QLabel("Output Folder"), 2, 0);
        experimentLayout->addWidget(outputDir, 2, 1);
        experimentLayout->addWidget(browseButton, 2, 2);

        auto captureLayout = new QHBoxLayout;
        captureLayout->addWidget(new QLabel("Resolution"));
        captureLayout->addWidget(resolutionBox);
        captureLayout->addWidget(new QLabel("FPS"));
        captureLayout->addWidget(fpsBox);
        captureLayout->addWidget(new QLabel("Mode"));
        captureLayout->addWidget(timedRadio);
        captureLayout->addWidget(continuousRadio);
        captureLayout->addWidget(new QLabel("Hours"));
        captureLayout->addWidget(hoursBox);
        captureLayout->addWidget(new QLabel("Minutes"));
        captureLayout->addWidget(minutesBox);
        captureLayout->addWidget(new QLabel("Seconds"));
        captureLayout->addWidget(secondsBox);


        auto cameraLayout = new QHBoxLayout;
        cameraLayout->addWidget(new QLabel("Exposure"));
        cameraLayout->addWidget(exposureBox);
        cameraLayout->addWidget(new QLabel("Gain"));
        cameraLayout->addWidget(gainBox);
        cameraLayout->addWidget(new QLabel("Screenshot Format"));
        cameraLayout->addWidget(screenshotFormatBox);
        cameraLayout->addWidget(new QLabel("Video Format"));
        cameraLayout->addWidget(videoFormatBox);

        auto presetLayout = new QHBoxLayout;
        presetLayout->addWidget(new QLabel("Saved Settings"));
        presetLayout->addWidget(savedSettingsBox);
        presetLayout->addWidget(loadSettingsButton);
        presetLayout->addWidget(new QLabel("Save Current As"));
        presetLayout->addWidget(settingNameEntry);
        presetLayout->addWidget(saveSettingsButton);

        auto advancedLayout = new QHBoxLayout;
        advancedLayout->addWidget(new QLabel("Gamma"));
        advancedLayout->addWidget(gammaBox);
        advancedLayout->addWidget(new QLabel("WB Red"));
        advancedLayout->addWidget(wbRBox);
        advancedLayout->addWidget(new QLabel("WB Green"));
        advancedLayout->addWidget(wbGBox);
        advancedLayout->addWidget(new QLabel("WB Blue"));
        advancedLayout->addWidget(wbBBox);
	advancedLayout->addWidget(autoStretchBox);
        advancedLayout->addWidget(droppedFramesLabel); 
        dvancedBox->setLayout(advancedLayout);

        auto buttons = new QHBoxLayout;
        buttons->addWidget(screenshotButton);
        buttons->addWidget(recordButton);
        buttons->addWidget(stopButton);

        auto layout = new QVBoxLayout;
        layout->addWidget(preview);
        layout->addWidget(cameraStatus);
        layout->addLayout(experimentLayout);
        layout->addLayout(captureLayout);
        layout->addLayout(cameraLayout);
        layout->addLayout(presetLayout);
        layout->addWidget(advancedBox);
        layout->addLayout(buttons);
        layout->addWidget(statusLabel);
        setLayout(layout);

        timer = new QTimer(this);

        connect(timer, &QTimer::timeout, this, &FlyStressCapture::grabFrame);
        connect(browseButton, &QPushButton::clicked, this, &FlyStressCapture::chooseFolder);
        connect(screenshotButton, &QPushButton::clicked, this, &FlyStressCapture::takeScreenshot);
        connect(recordButton, &QPushButton::clicked, this, &FlyStressCapture::startRecording);
        connect(stopButton, &QPushButton::clicked, this, &FlyStressCapture::stopRecording);
        connect(saveSettingsButton, &QPushButton::clicked, this, &FlyStressCapture::savePreset);
        connect(loadSettingsButton, &QPushButton::clicked, this, &FlyStressCapture::loadPreset);
        connect(timedRadio, &QRadioButton::toggled, this, &FlyStressCapture::updateDurationEnabled);

        loadPresets();
        loadAppState();
        updateDurationEnabled();
        openCamera();
    }

    ~FlyStressCapture()
    {
        saveAppState();
        stopRecording();
        timer->stop();

        if (cameraOpen)
        {
            SVBStopVideoCapture(cameraID);
            SVBCloseCamera(cameraID);
        }
    }

private:
    QLabel *preview;
    QLabel *cameraStatus;
    QLabel *statusLabel;
    QLabel *droppedFramesLabel;

    QLineEdit *experimentName;
    QTextEdit *notes;
    QLineEdit *outputDir;
    QPushButton *browseButton;

    QComboBox *resolutionBox;
    QSpinBox *fpsBox;

    QRadioButton *timedRadio;
    QRadioButton *continuousRadio;
    QButtonGroup *modeGroup;

    QSpinBox *hoursBox;
    QSpinBox *minutesBox;
    QSpinBox *secondsBox;

    QSpinBox *exposureBox;
    QSpinBox *gainBox;

    QComboBox *screenshotFormatBox;
    QComboBox *videoFormatBox;

    QComboBox *savedSettingsBox;
    QLineEdit *settingNameEntry;
    QPushButton *loadSettingsButton;
    QPushButton *saveSettingsButton;

    QPushButton *screenshotButton;
    QPushButton *recordButton;
    QPushButton *stopButton;

    QGroupBox *advancedBox;
    QSpinBox *gammaBox;
    QSpinBox *wbRBox;
    QSpinBox *wbGBox;
    QSpinBox *wbBBox;

    QTimer *timer;
    QCheckBox *autoStretchBox;

    int cameraID = -1;
    QString cameraName = "Unknown";
    QString cameraSN = "Unknown";

    bool cameraOpen = false;
    bool recording = false;

    int width = 1920;
    int height = 1080;
    int frameIndex = 0;
    int droppedFrames = 0;

    cv::Mat latestFrame;
    cv::VideoWriter writer;

    QString currentSessionDir;
    QString currentRecordingPath;

    std::chrono::steady_clock::time_point recordStart;

    QString appDir = "/home/admin/FlyStressCapture";
    QString presetsPath = "/home/admin/FlyStressCapture/saved_settings.json";
    QString appStatePath = "/home/admin/FlyStressCapture/app_state.json";

    QJsonObject presets;

    int totalDurationSeconds() const
    {
        if (continuousRadio->isChecked())
            return 0;

        return hoursBox->value() * 3600 +
               minutesBox->value() * 60 +
               secondsBox->value();
    }

    QString timestamp() const
    {
        return QDateTime::currentDateTime().toString("yyyy-MM-dd_HH-mm-ss");
    }

    QString safeName(QString name) const
    {
        name = name.trimmed();
        name.replace(" ", "_");

        for (QChar &c : name)
        {
            if (!c.isLetterOrNumber() && c != '_' && c != '-')
                c = '_';
        }

        if (name.isEmpty())
            name = "Experiment";

        return name;
    }

    QString makeSessionDir()
    {
        QString base = outputDir->text();
        QString exp = experimentName->text().trimmed();

        QString folderName;
        if (exp.isEmpty())
            folderName = timestamp();
        else
            folderName = safeName(exp) + "_" + timestamp();

        QString dir = base + "/" + folderName;
        fs::create_directories(dir.toStdString());

        return dir;
    }

    bool openCamera()
    {
        int cameraNum = SVBGetNumOfConnectedCameras();

        if (cameraNum <= 0)
        {
            QMessageBox::critical(this, "Camera Error", "No SVBONY camera found.");
            return false;
        }

        SVB_CAMERA_INFO info;
        SVBGetCameraInfo(&info, 0);

        cameraID = info.CameraID;
        cameraName = info.FriendlyName;
        cameraSN = info.CameraSN;

        if (SVBOpenCamera(cameraID) != SVB_SUCCESS)
        {
            QMessageBox::critical(this, "Camera Error", "Could not open SVBONY camera.");
            return false;
        }

        cameraOpen = true;

        SVBSetCameraMode(cameraID, SVB_MODE_NORMAL);
        SVBSetAutoSaveParam(cameraID, SVB_FALSE);

        applyCameraSettings();

        if (SVBStartVideoCapture(cameraID) != SVB_SUCCESS)
        {
            QMessageBox::critical(this, "Camera Error", "Could not start video capture.");
            return false;
        }

        cameraStatus->setText("Camera: " + cameraName + " | Connected");
        timer->start(1000 / fpsBox->value());

        return true;
    }

    void applyCameraSettings()
    {
        if (!cameraOpen)
            return;

        QSize size = resolutionBox->currentData().toSize();
        width = size.width();
        height = size.height();

        SVBSetROIFormat(cameraID, 0, 0, width, height, 1);
        SVBSetOutputImageType(cameraID, SVB_IMG_RAW8);

        SVBSetControlValue(cameraID, SVB_EXPOSURE, exposureBox->value(), SVB_FALSE);
        SVBSetControlValue(cameraID, SVB_GAIN, gainBox->value(), SVB_FALSE);
        SVBSetControlValue(cameraID, SVB_GAMMA, gammaBox->value(), SVB_FALSE);
        SVBSetControlValue(cameraID, SVB_WB_R, wbRBox->value(), SVB_FALSE);
        SVBSetControlValue(cameraID, SVB_WB_G, wbGBox->value(), SVB_FALSE);
        SVBSetControlValue(cameraID, SVB_WB_B, wbBBox->value(), SVB_FALSE);
    }

    void grabFrame()
    {
        if (!cameraOpen)
            return;

        timer->setInterval(1000 / fpsBox->value());
        applyCameraSettings();

        int bufferSize = width * height;
        std::vector<unsigned char> buffer(bufferSize);

        SVB_ERROR_CODE ret = SVBGetVideoData(cameraID, buffer.data(), bufferSize, 1000);

        if (ret != SVB_SUCCESS)
        {
            droppedFrames++;
            droppedFramesLabel->setText(QString("Dropped frames: %1").arg(droppedFrames));
            return;
        }

        cv::Mat raw(height, width, CV_8UC1, buffer.data());
/*	cv::Mat_displayRaw = raw.clone();


	if (autoStretchBox->isChecked())
	{
		double minVal, maxVal;
		cv::minMaxLoc(displayRaw, &minVal, &maxVal);

		if (maxVal > minVal)
		{
			displayRaw.convertTo(
				displayRaw,
				CV_8U,
				255.0 / (maxVal - minVal),
				-minVal * 255.0 / (maxVal - minVal)
			);
		}
	}
*/	
	cv::Mat bgr;
	cv::cvtColor(displayRaw, bgr, cv::COLOR_BayerBG2BGR);

	latestFrame = bgr.clone();

        if (recording)
            writeRecordingFrame();

        showPreview();
    }

    void showPreview()
    {
        if (latestFrame.empty())
		return;
	

	cv::Mat rgb;
	cv::cvtColor(latestFrame, rgb, cv::COLOR_BGR2RGB);

	QImage image(
		rgb.data,
		rgb.cols,
		rgb.rows,
		rgb.step,
		QImage::Format_RGB888
	);

	preview->setPixmap(
		QPixmap::fromImage(image).scaled(
			preview->width(),
			preview->height(),
			Qt::KeepAspectRatio
		)
	);
}
	

    void writeRecordingFrame()
    {
        writer.write(latestFrame);
        frameIndex++;

        double elapsed = std::chrono::duration<double>(
            std::chrono::steady_clock::now() - recordStart
        ).count();

        int duration = totalDurationSeconds();
        QString remainingText = "Continuous";

        if (duration > 0)
        {
            double remaining = duration - elapsed;
            if (remaining < 0)
                remaining = 0;

            remainingText = formatTime((int)remaining);

            if (elapsed >= duration)
            {
                stopRecording();
                return;
            }
        }

        statusLabel->setText(
            QString("Recording | Elapsed: %1 | Remaining: %2 | Frames: %3 | Dropped: %4")
            .arg(formatTime((int)elapsed))
            .arg(remainingText)
            .arg(frameIndex)
            .arg(droppedFrames)
        );
    }

    QString formatTime(int seconds) const
    {
        int h = seconds / 3600;
        int m = (seconds % 3600) / 60;
        int s = seconds % 60;

        return QString("%1:%2:%3")
            .arg(h, 2, 10, QChar('0'))
            .arg(m, 2, 10, QChar('0'))
            .arg(s, 2, 10, QChar('0'));
    }

    void chooseFolder()
    {
        QString dir = QFileDialog::getExistingDirectory(this, "Choose Output Folder", outputDir->text());

        if (!dir.isEmpty())
            outputDir->setText(dir);
    }

    void updateDurationEnabled()
    {
        bool timed = timedRadio->isChecked();

        hoursBox->setEnabled(timed);
        minutesBox->setEnabled(timed);
        secondsBox->setEnabled(timed);
    }

    QJsonObject currentMetadata(QString captureType, QString filePath)
    {
        QSize size = resolutionBox->currentData().toSize();

        QJsonObject obj;
        obj["capture_type"] = captureType;
        obj["file_path"] = filePath;
        obj["experiment_name"] = experimentName->text();
        obj["notes"] = notes->toPlainText();
        obj["timestamp"] = QDateTime::currentDateTime().toString(Qt::ISODate);

        obj["camera_name"] = cameraName;
        obj["camera_serial_number"] = cameraSN;

        obj["width"] = size.width();
        obj["height"] = size.height();
        obj["fps"] = fpsBox->value();

        obj["recording_mode"] = timedRadio->isChecked() ? "Timed" : "Continuous";
        obj["duration_seconds"] = totalDurationSeconds();

        obj["exposure_us"] = exposureBox->value();
        obj["gain"] = gainBox->value();
        obj["gamma"] = gammaBox->value();

        QJsonObject wb;
        wb["red"] = wbRBox->value();
        wb["green"] = wbGBox->value();
        wb["blue"] = wbBBox->value();
        obj["white_balance"] = wb;

        obj["screenshot_format"] = screenshotFormatBox->currentText();
        obj["video_format"] = videoFormatBox->currentText();
        obj["dropped_frames"] = droppedFrames;

        return obj;
    }

    void writeJson(QString path, QJsonObject obj)
    {
        QFile file(path);
        if (file.open(QIODevice::WriteOnly))
        {
            file.write(QJsonDocument(obj).toJson(QJsonDocument::Indented));
            file.close();
        }
    }

    void takeScreenshot()
    {
        if (latestFrame.empty())
        {
            QMessageBox::warning(this, "No Frame", "No frame available yet.");
            return;
        }

        QString dir = makeSessionDir();

        QString ext = screenshotFormatBox->currentText() == "TIFF" ? "tiff" : "png";
        QString imagePath = dir + "/screenshot." + ext;
        QString jsonPath = dir + "/screenshot.json";

        cv::imwrite(imagePath.toStdString(), latestFrame);
        writeJson(jsonPath, currentMetadata("screenshot", imagePath));

        QMessageBox::information(this, "Screenshot Saved", "Saved:\n" + imagePath);
    }

    void startRecording()
    {
        if (latestFrame.empty())
        {
            QMessageBox::warning(this, "No Frame", "No frame available yet.");
            return;
        }

        if (timedRadio->isChecked() && totalDurationSeconds() <= 0)
        {
            QMessageBox::warning(this, "Invalid Duration", "Timed recording needs a duration greater than zero.");
            return;
        }

        applyCameraSettings();

        currentSessionDir = makeSessionDir();

        QString ext = videoFormatBox->currentText().startsWith("MP4") ? "mp4" : "avi";
        currentRecordingPath = currentSessionDir + "/recording." + ext;

        int fourcc;

        if (ext == "mp4")
            fourcc = cv::VideoWriter::fourcc('m', 'p', '4', 'v');
        else
            fourcc = cv::VideoWriter::fourcc('M', 'J', 'P', 'G');

        writer.open(
            currentRecordingPath.toStdString(),
            fourcc,
            fpsBox->value(),
            cv::Size(width, height),
            true
        );

        if (!writer.isOpened())
        {
            QMessageBox::critical(this, "Recording Error", "Could not start video writer.");
            return;
        }

        recording = true;
        frameIndex = 0;
        droppedFrames = 0;
        recordStart = std::chrono::steady_clock::now();

        recordButton->setEnabled(false);
        stopButton->setEnabled(true);

        statusLabel->setText("Recording started...");
    }

    void stopRecording()
    {
        if (!recording)
            return;

        recording = false;

        if (writer.isOpened())
            writer.release();

        QString jsonPath = currentSessionDir + "/recording.json";
        QJsonObject meta = currentMetadata("recording", currentRecordingPath);
        meta["frames_recorded"] = frameIndex;
        writeJson(jsonPath, meta);

        recordButton->setEnabled(true);
        stopButton->setEnabled(false);

        statusLabel->setText("Recording stopped | Frames: " + QString::number(frameIndex));
    }

    QJsonObject currentPresetObject()
    {
        QSize size = resolutionBox->currentData().toSize();

        QJsonObject obj;
        obj["width"] = size.width();
        obj["height"] = size.height();
        obj["fps"] = fpsBox->value();
        obj["timed"] = timedRadio->isChecked();
        obj["hours"] = hoursBox->value();
        obj["minutes"] = minutesBox->value();
        obj["seconds"] = secondsBox->value();
        obj["exposure"] = exposureBox->value();
        obj["gain"] = gainBox->value();
        obj["gamma"] = gammaBox->value();
        obj["wb_r"] = wbRBox->value();
        obj["wb_g"] = wbGBox->value();
        obj["wb_b"] = wbBBox->value();
        obj["screenshot_format"] = screenshotFormatBox->currentText();
        obj["video_format"] = videoFormatBox->currentText();
        obj["output_dir"] = outputDir->text();

        return obj;
    }

    void applyPresetObject(QJsonObject obj)
    {
        int w = obj["width"].toInt(1920);
        int h = obj["height"].toInt(1080);

        for (int i = 0; i < resolutionBox->count(); i++)
        {
            QSize size = resolutionBox->itemData(i).toSize();

            if (size.width() == w && size.height() == h)
            {
                resolutionBox->setCurrentIndex(i);
                break;
            }
        }

        fpsBox->setValue(obj["fps"].toInt(10));

        if (obj["timed"].toBool(true))
            timedRadio->setChecked(true);
        else
            continuousRadio->setChecked(true);

        hoursBox->setValue(obj["hours"].toInt(0));
        minutesBox->setValue(obj["minutes"].toInt(10));
        secondsBox->setValue(obj["seconds"].toInt(0));

        exposureBox->setValue(obj["exposure"].toInt(1000));
        gainBox->setValue(obj["gain"].toInt(0));
        gammaBox->setValue(obj["gamma"].toInt(100));
        wbRBox->setValue(obj["wb_r"].toInt(128));
        wbGBox->setValue(obj["wb_g"].toInt(128));
        wbBBox->setValue(obj["wb_b"].toInt(128));

        screenshotFormatBox->setCurrentText(obj["screenshot_format"].toString("PNG"));
        videoFormatBox->setCurrentText(obj["video_format"].toString("AVI MJPEG"));
        outputDir->setText(obj["output_dir"].toString("/home/admin/Desktop/VideosA"));

        updateDurationEnabled();
        applyCameraSettings();
    }

    void loadPresets()
    {
        fs::create_directories(appDir.toStdString());

        presets = QJsonObject();
        savedSettingsBox->clear();

        QFile file(presetsPath);

        if (file.exists() && file.open(QIODevice::ReadOnly))
        {
            QJsonDocument doc = QJsonDocument::fromJson(file.readAll());
            file.close();

            if (doc.isObject())
                presets = doc.object();
        }

        for (const QString &key : presets.keys())
            savedSettingsBox->addItem(key);
    }

    void savePreset()
    {
        QString name = settingNameEntry->text().trimmed();

        if (name.isEmpty())
        {
            QMessageBox::warning(this, "Missing Name", "Enter a name for the saved settings.");
            return;
        }

        presets[name] = currentPresetObject();

        QFile file(presetsPath);
        if (!file.open(QIODevice::WriteOnly))
        {
            QMessageBox::critical(this, "Save Error", "Could not save preset file.");
            return;
        }

        file.write(QJsonDocument(presets).toJson(QJsonDocument::Indented));
        file.close();

        loadPresets();
        savedSettingsBox->setCurrentText(name);

        QMessageBox::information(this, "Saved", "Saved settings:\n" + name);
    }

    void loadPreset()
    {
        QString name = savedSettingsBox->currentText();

        if (name.isEmpty() || !presets.contains(name))
            return;

        applyPresetObject(presets[name].toObject());

        QMessageBox::information(this, "Loaded", "Loaded settings:\n" + name);
    }

    void saveAppState()
    {
        fs::create_directories(appDir.toStdString());

        QJsonObject obj = currentPresetObject();
        obj["experiment_name"] = experimentName->text();
        obj["notes"] = notes->toPlainText();
        obj["selected_preset"] = savedSettingsBox->currentText();

        QFile file(appStatePath);
        if (file.open(QIODevice::WriteOnly))
        {
            file.write(QJsonDocument(obj).toJson(QJsonDocument::Indented));
            file.close();
        }
    }

    void loadAppState()
    {
        QFile file(appStatePath);

        if (!file.exists() || !file.open(QIODevice::ReadOnly))
            return;

        QJsonDocument doc = QJsonDocument::fromJson(file.readAll());
        file.close();

        if (!doc.isObject())
            return;

        QJsonObject obj = doc.object();

        experimentName->setText(obj["experiment_name"].toString());
        notes->setPlainText(obj["notes"].toString());

        applyPresetObject(obj);

        QString selectedPreset = obj["selected_preset"].toString();
        if (!selectedPreset.isEmpty())
            savedSettingsBox->setCurrentText(selectedPreset);
    }
};

#include "main.moc"

int main(int argc, char *argv[])
{
    QApplication app(argc, argv);

    FlyStressCapture window;
    window.show();

    return app.exec();
}
