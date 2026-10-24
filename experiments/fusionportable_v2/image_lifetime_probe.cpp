#include <cassert>
#include <iostream>
#include <memory>
#include <vector>
#include <cv_bridge/cv_bridge.h>
#include <opencv2/imgproc.hpp>

// Exercise the cv_bridge ownership contract used by LIVMapper's image queue.
// Never dereference the old borrowed pixels after their ROS owner expires.
int main()
{
  auto message = std::make_shared<sensor_msgs::msg::Image>();
  message->height = 768;
  message->width = 1024;
  message->encoding = "bgr8";
  message->step = message->width * 3;
  message->data.assign(message->height * message->step, 17);
  std::weak_ptr<sensor_msgs::msg::Image> owner = message;
  auto bridge = cv_bridge::toCvShare(message, "bgr8");
  cv::Mat borrowed = bridge->image;
  assert(borrowed.data == message->data.data());
  assert(borrowed.u == nullptr);
  bridge.reset();
  message.reset();
  assert(owner.expired());

  auto input = std::make_shared<sensor_msgs::msg::Image>();
  input->height = 768;
  input->width = 1024;
  input->encoding = "bgr8";
  input->step = input->width * 3;
  input->data.assign(input->height * input->step, 29);
  cv::Mat owned = cv_bridge::toCvCopy(input, "bgr8")->image;
  assert(owned.data != input->data.data());
  assert(owned.u != nullptr);
  std::weak_ptr<sensor_msgs::msg::Image> copied_owner = input;
  input.reset();
  assert(copied_owner.expired());
  std::vector<std::vector<unsigned char>> churn(20, std::vector<unsigned char>(768 * 1024 * 3, 99));
  cv::Mat resized;
  cv::resize(owned, resized, cv::Size(512, 384));
  assert(cv::countNonZero(resized.reshape(1) != 29) == 0);
  std::cout << "{\"borrowed_mat_loses_ros_owner\":true,\"copied_mat_survives_owner_release_and_resize\":true}" << std::endl;
}
