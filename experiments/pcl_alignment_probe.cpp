#include <pcl/filters/voxel_grid.h>
#include <pcl/point_types.h>
#include <pcl/point_cloud.h>
#include <iostream>

int main() {
  std::cout << "Eigen alignment=" << EIGEN_DEFAULT_ALIGN_BYTES
            << " malloc_already_aligned=" << EIGEN_MALLOC_ALREADY_ALIGNED << std::endl;
  pcl::PointCloud<pcl::PointXYZINormal>::Ptr input(new pcl::PointCloud<pcl::PointXYZINormal>);
  pcl::PointCloud<pcl::PointXYZINormal>::Ptr output(new pcl::PointCloud<pcl::PointXYZINormal>);
  input->resize(100);
  for (size_t i = 0; i < input->size(); ++i) {
    (*input)[i].x = static_cast<float>(i) * 0.1f;
    (*input)[i].y = 1.0f;
    (*input)[i].z = 2.0f;
  }
  pcl::VoxelGrid<pcl::PointXYZINormal> filter;
  filter.setInputCloud(input);
  filter.setLeafSize(0.2f, 0.2f, 0.2f);
  filter.filter(*output);
  std::cout << "Filtered " << output->size() << " points" << std::endl;
}
